"""Deterministic replay. No LLM. Walk the steps, verify checkpoints, and
classify anything unexpected into business outcome vs hard failure.
"""
from __future__ import annotations

import uuid

from .driver import Driver
from .models import (BusinessOutcome, CapabilityArtifact, Failure, Result,
                     Success)
from .safety import SafetyGate


def _check(obs, checkpoint: str) -> bool:
    # checkpoint format: "screen==detail" or "field:savings_balance"
    if checkpoint.startswith("screen=="):
        return obs.screen == checkpoint.split("==", 1)[1]
    if checkpoint.startswith("field:"):
        return checkpoint.split(":", 1)[1] in obs.fields
    return True


def replay(artifact: CapabilityArtifact, inputs: dict, driver: Driver,
           gate: SafetyGate) -> Result:
    run_id = str(uuid.uuid4())[:8]

    for step in artifact.steps:
        decision = gate.check(step.action)
        if decision == "deny":
            return Failure(step=step.id, expected="allowed action",
                           observed="blocked by allowlist", run_id=run_id)

        value = None
        if step.value_from and step.value_from.startswith("input."):
            value = inputs.get(step.value_from.split(".", 1)[1])

        driver.act(step.action, step.target, value)
        obs = driver.perceive()

        # known business outcome? (e.g. landed on "not found")
        for ko in artifact.known_outcomes:
            if _check(obs, ko.detect):
                return BusinessOutcome(name=ko.name, run_id=run_id)

        # step checkpoint must hold, else hard failure
        if step.checkpoint and not _check(obs, step.checkpoint):
            return Failure(step=step.id, expected=step.checkpoint,
                           observed=f"screen={obs.screen}", run_id=run_id)

    # success checkpoint + collect declared outputs
    obs = driver.perceive()
    if not _check(obs, artifact.success):
        return Failure(step="success", expected=artifact.success,
                       observed=f"screen={obs.screen}", run_id=run_id)

    outputs = {o.name: obs.fields.get(o.name) for o in artifact.outputs}
    if any(v is None for v in outputs.values()):
        missing = [k for k, v in outputs.items() if v is None]
        return Failure(step="success", expected=f"outputs {missing} present",
                       observed="missing on page", run_id=run_id)

    return Success(outputs=outputs, run_id=run_id)
