"""Deterministic replay. No LLM. Walk the steps, verify checkpoints, and
classify anything unexpected into a recoverable condition, a business
outcome, or a hard failure.
"""
from __future__ import annotations

import uuid

from .driver import Driver
from .models import (BusinessOutcome, CapabilityArtifact, Failure,
                     RecoveredEvent, Result, Success)
from .safety import SafetyGate

# Bounded, on purpose: a recoverable condition gets a few short chances to
# resolve itself, never an open-ended wait. Once these are exhausted it
# becomes exactly the hard failure it would have been without this at all.
MAX_SLOW_RETRIES = 3
MAX_INTERSTITIAL_DISMISSALS = 3


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


def replay(artifact: CapabilityArtifact, inputs: dict, driver: Driver,
           gate: SafetyGate) -> Result:
    run_id = str(uuid.uuid4())[:8]
    recovered: list[RecoveredEvent] = []

    for step in artifact.steps:
        decision = gate.check(step.action)
        if decision == "deny":
            return Failure(step=step.id, expected="allowed action",
                           observed="blocked by allowlist", run_id=run_id,
                           recovered=recovered)

        value = None
        if step.value_from and step.value_from.startswith("input."):
            value = inputs.get(step.value_from.split(".", 1)[1])

        driver.act(step.action, step.target, value)
        obs = driver.perceive()
        obs = _dismiss_known_interstitials(artifact, driver, obs, step.id, recovered)

        # known business outcome? (e.g. landed on "not found")
        for ko in artifact.known_outcomes:
            if _check(obs, ko.detect):
                return BusinessOutcome(name=ko.name, run_id=run_id,
                                       recovered=recovered)

        # step checkpoint: bounded retries for a slow-to-settle page, then
        # a hard failure if it never resolves.
        obs = _await_checkpoint(driver, obs, step.checkpoint, step.id, recovered)
        if step.checkpoint and not _check(obs, step.checkpoint):
            return Failure(step=step.id, expected=step.checkpoint,
                           observed=f"screen={obs.screen}", run_id=run_id,
                           recovered=recovered)

    # success checkpoint + collect declared outputs
    obs = driver.perceive()
    obs = _await_checkpoint(driver, obs, artifact.success, "success", recovered)
    if not _check(obs, artifact.success):
        return Failure(step="success", expected=artifact.success,
                       observed=f"screen={obs.screen}", run_id=run_id,
                       recovered=recovered)

    outputs = {o.name: obs.fields.get(o.name) for o in artifact.outputs}
    if any(v is None for v in outputs.values()):
        missing = [k for k, v in outputs.items() if v is None]
        return Failure(step="success", expected=f"outputs {missing} present",
                       observed="missing on page", run_id=run_id,
                       recovered=recovered)

    return Success(outputs=outputs, run_id=run_id, recovered=recovered)
