"""Deterministic replay. No LLM. Walk the steps, verify checkpoints, and
classify anything unexpected into a recoverable condition, a business
outcome, or a hard failure.
"""
from __future__ import annotations

import uuid
from typing import Callable, Optional

from .driver import Driver
from .models import (BusinessOutcome, CapabilityArtifact, Failure, Paused,
                     RecoveredEvent, Result, ResumeState, Success)
from .safety import SafetyGate

# Bounded, on purpose: a recoverable condition gets a few short chances to
# resolve itself, never an open-ended wait. Once these are exhausted it
# becomes exactly the hard failure it would have been without this at all.
MAX_SLOW_RETRIES = 3
MAX_INTERSTITIAL_DISMISSALS = 3

# Called (context: dict, screenshot: bytes | None) exactly when replay is
# about to return a Failure or a Paused -- a genuine stuck state, not a
# business outcome. Never called for Success or BusinessOutcome. context is
# already redacted; screenshot is raw and left to the caller to persist.
OnStuck = Callable[[dict, Optional[bytes]], None]


def _check(obs, checkpoint: str) -> bool:
    # checkpoint format: "screen==detail" or "field:savings_balance"
    if checkpoint.startswith("screen=="):
        return obs.screen == checkpoint.split("==", 1)[1]
    if checkpoint.startswith("field:"):
        return checkpoint.split(":", 1)[1] in obs.fields
    return True


def _dismiss_known_interstitials(artifact, driver, obs, step_id, recovered):
    """If a recognized dismissable overlay is showing, dismiss it and
    re-perceive. Bounded to MAX_INTERSTITIAL_DISMISSALS for the whole run,
    not per step, so a misbehaving page can't loop this forever.
    """
    dismissals = 0
    while dismissals < MAX_INTERSTITIAL_DISMISSALS:
        hit = next((ki for ki in artifact.known_interstitials
                   if _check(obs, ki.detect)), None)
        if hit is None:
            break
        driver.act(hit.dismiss_action, hit.dismiss, None)
        obs = driver.perceive()
        dismissals += 1
        recovered.append(RecoveredEvent(step=step_id,
                                        condition=f"interstitial:{hit.name}",
                                        attempts=dismissals))
    return obs


def _await_checkpoint(driver, obs, checkpoint, step_id, recovered):
    """Give a checkpoint a small, bounded number of extra chances to become
    true (a transient slow load) before the caller treats it as failed.
    Each retry is one more short, bounded wait from the driver, never a
    fixed sleep and never open-ended.
    """
    if not checkpoint or _check(obs, checkpoint):
        return obs
    attempts = 0
    while attempts < MAX_SLOW_RETRIES and not _check(obs, checkpoint):
        driver.act("wait")
        obs = driver.perceive()
        attempts += 1
    if attempts and _check(obs, checkpoint):
        recovered.append(RecoveredEvent(step=step_id, condition="slow_load",
                                        attempts=attempts))
    return obs


def _fail(artifact, gate, on_stuck, recovered, run_id, obs, step_id,
          step_index, expected, observed, resumable):
    """Builds the stuck-state result and, if an on_stuck hook is wired,
    auto-raises a handoff request carrying real context before returning it,
    the same shape a human calling /handoff/request would create, just
    triggered by the system instead of a person.

    Returns a Paused if the caller opted into resumability, a Failure
    otherwise -- the same stuck condition, classified differently only by
    whether anyone asked for the chance to resume it. Only ever called on a
    genuine stuck state; Success and BusinessOutcome never go through this.
    """
    reason = f"expected {expected}, observed {observed}"
    if on_stuck:
        snapshot = gate.redact({"screen": obs.screen, "fields": dict(obs.fields)})
        context = {
            "capability": artifact.name,
            "step": step_id,
            "reason": reason,
            "snapshot": snapshot,
        }
        on_stuck(context, obs.screenshot)
    if resumable:
        return Paused(step=step_id, reason=reason, run_id=run_id,
                     resume=ResumeState(run_id=run_id, step_index=step_index,
                                        recovered=recovered))
    return Failure(step=step_id, expected=expected, observed=observed,
                   run_id=run_id, recovered=recovered)


def replay(artifact: CapabilityArtifact, inputs: dict, driver: Driver,
           gate: SafetyGate, on_stuck: Optional[OnStuck] = None,
           resumable: bool = False,
           resume: Optional[ResumeState] = None) -> Result | Paused:
    """Runs (or resumes) a capability against a live driver session.

    resumable=False (the default): behaves exactly as before. A stuck state
    returns Failure. No caller that predates pause/resume sees any change.

    resumable=True: a stuck state returns Paused instead of Failure, with a
    ResumeState pointing at the exact step it stopped on. Pass that same
    ResumeState back in as `resume` on a later call, using the SAME driver
    (its session was never torn down), to continue from there: steps before
    it are not re-run, and the step it paused on is only re-run if its
    `retry_safe` is true -- otherwise replay just re-perceives current state
    and re-checks, on the assumption a human already made the needed change
    without needing the action redone.
    """
    if resume:
        run_id, recovered, start_index = resume.run_id, list(resume.recovered), resume.step_index
    else:
        run_id, recovered, start_index = str(uuid.uuid4())[:8], [], 0

    for i, step in enumerate(artifact.steps):
        if i < start_index:
            continue  # already completed before the pause

        if i == start_index and resume is not None and not step.retry_safe:
            # Not safe to redo (e.g. a submit): trust the step already ran
            # before the pause, just re-read what the session looks like now.
            obs = driver.perceive()
        else:
            decision = gate.check(step.action)
            if decision == "deny":
                obs = driver.perceive()
                return _fail(artifact, gate, on_stuck, recovered, run_id, obs,
                            step.id, i, "allowed action", "blocked by allowlist",
                            resumable)

            value = None
            if step.value_from and step.value_from.startswith("input."):
                value = inputs.get(step.value_from.split(".", 1)[1])

            driver.act(step.action, step.target, value)
            obs = driver.perceive()

        obs = _dismiss_known_interstitials(artifact, driver, obs, step.id, recovered)

        # known business outcome? (e.g. landed on "not found") -- a valid
        # answer, never routed through _fail, never raises a handoff.
        for ko in artifact.known_outcomes:
            if _check(obs, ko.detect):
                return BusinessOutcome(name=ko.name, run_id=run_id,
                                       recovered=recovered)

        # step checkpoint: bounded retries for a slow-to-settle page, then
        # a hard failure (or a pause) if it never resolves.
        obs = _await_checkpoint(driver, obs, step.checkpoint, step.id, recovered)
        if step.checkpoint and not _check(obs, step.checkpoint):
            return _fail(artifact, gate, on_stuck, recovered, run_id, obs,
                        step.id, i, step.checkpoint, f"screen={obs.screen}",
                        resumable)

    # success checkpoint + collect declared outputs
    obs = driver.perceive()
    obs = _await_checkpoint(driver, obs, artifact.success, "success", recovered)
    if not _check(obs, artifact.success):
        return _fail(artifact, gate, on_stuck, recovered, run_id, obs,
                    "success", len(artifact.steps), artifact.success,
                    f"screen={obs.screen}", resumable)

    outputs = {o.name: obs.fields.get(o.name) for o in artifact.outputs}
    if any(v is None for v in outputs.values()):
        missing = [k for k, v in outputs.items() if v is None]
        return _fail(artifact, gate, on_stuck, recovered, run_id, obs,
                    "success", len(artifact.steps), f"outputs {missing} present",
                    "missing on page", resumable)

    return Success(outputs=outputs, run_id=run_id, recovered=recovered)
