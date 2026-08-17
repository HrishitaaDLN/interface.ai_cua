"""Reconciles the two draft artifacts from step 4's discovery runs into one
approved recipe. Deliberately NOT a blind union.

The success draft's steps are kept exactly as discovered -- the click
step's checkpoint stays "screen==detail", the happy-path assertion. The
only change is folding in the not-found draft's single known_outcome. This
is safe because replay.py already checks known_outcomes BEFORE a step's own
checkpoint: on the 99999 path, "screen==not_found" matches and returns
BusinessOutcome before the click step's "screen==detail" checkpoint ever
gets a chance to fail. The two branches coexist without the click step
needing to know about the not-found path at all.

Stands in for a human operator reviewing both discovery runs and approving
one merged capability. Run once:
    python scripts/merge_and_approve.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.models import ApprovalState, CapabilityArtifact
from app.safety import SafetyGate, redaction_keys_from_artifact

SUCCESS_DRAFT = ROOT / "evidence" / "discovery-run" / "draft_artifact.json"
NOTFOUND_DRAFT = ROOT / "evidence" / "discovery-run-notfound" / "draft_artifact.json"
OUT_DIR = ROOT / "evidence" / "approved-recipe"
OUT_PATH = OUT_DIR / "lookup_savings.json"

gate = SafetyGate(allowed_actions={"type", "click", "read", "wait", "navigate"},
                  redact_keys={"ssn", "password", "token"})


def main() -> None:
    success = CapabilityArtifact.model_validate_json(SUCCESS_DRAFT.read_text())
    notfound = CapabilityArtifact.model_validate_json(NOTFOUND_DRAFT.read_text())

    if len(notfound.known_outcomes) != 1:
        raise SystemExit(
            f"expected exactly one known_outcome in {NOTFOUND_DRAFT}, "
            f"got {len(notfound.known_outcomes)} -- not merging blindly")

    merged = success.model_copy(deep=True)
    merged.id = "cap_lookup_savings"
    merged.name = "lookup_savings"
    # merged.steps is untouched -- same object as success.steps, same
    # locator ladders, same checkpoints. The only substantive change:
    merged.known_outcomes = list(notfound.known_outcomes)
    merged.approval_state = ApprovalState.approved
    merged.provenance = {
        "recorded_by": "real_discovery+manual_merge",
        "goal": success.provenance.get("goal", ""),
        "merged_from": (f"{SUCCESS_DRAFT.relative_to(ROOT).as_posix()},"
                        f"{NOTFOUND_DRAFT.relative_to(ROOT).as_posix()}"),
        "approved_by": "operator (this exercise)",
    }

    # Every evidence write goes through the one redact() function, even
    # here where there's nothing sensitive in this particular artifact --
    # value_from bindings mean no literal runtime data ever lands in a
    # recipe in the first place, but routing through it anyway means a
    # future sensitive input field doesn't silently skip redaction.
    redacted = gate.redact(merged.model_dump(mode="json"),
                           extra_keys=redaction_keys_from_artifact(merged.redaction))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(redacted, indent=2))

    print("steps (unchanged from success draft):")
    for s in merged.steps:
        print(f"  {s.id} action={s.action} checkpoint={s.checkpoint!r}")
    print(f"known_outcomes added: {[o.model_dump() for o in merged.known_outcomes]}")
    print(f"approval_state: {merged.approval_state}")
    print(f"written to: {OUT_PATH}")


if __name__ == "__main__":
    main()
