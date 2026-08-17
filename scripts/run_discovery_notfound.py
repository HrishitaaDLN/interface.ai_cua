"""Runs ONE real discovery run against member 99999, who does not exist in
the seed data. The point is for the model to genuinely recognize the "no
such member" screen as a business outcome and for the loop to compile a
known_outcome from it -- not to treat it as a stuck/failed run.

Kept entirely separate from evidence/discovery-run/ (the successful member
12345 run) -- this is its own draft artifact, not merged into that one.

Precondition: the legacy bank server must already be running:
    uvicorn legacy_bank.server:app --port 8080

Run:
    python scripts/run_discovery_notfound.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.discovery import run_discovery
from app.driver import PlaywrightBankDriver
from app.safety import SafetyGate

GOAL = "look up member 99999 and read their savings balance"
ENTRY_POINT = "http://localhost:8080/core-banking"
EVIDENCE_DIR = ROOT / "evidence" / "discovery-run-notfound"

gate = SafetyGate(allowed_actions={"type", "click", "read", "wait", "navigate"},
                  redact_keys={"ssn", "password", "token"})


def main() -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    with PlaywrightBankDriver(headless=True) as driver:
        result = run_discovery(
            driver, goal=GOAL, entry_point=ENTRY_POINT,
            inputs={"member_id": "99999"},
            # The declared capability goal is unchanged -- this run just
            # never reaches it, by definition of hitting a business outcome.
            success_checkpoint="field:savings_balance",
            artifact_id="cap_lookup_savings_notfound_discovered",
            artifact_name="lookup_savings_notfound_discovered",
        )

    step_log = {
        "goal": result.goal,
        "model": result.model_name,
        "model_calls": result.model_calls,
        "stop_reason": result.stop_reason,
        "known_outcome": {"name": result.known_outcome.name,
                          "detect": result.known_outcome.detect,
                          "returns": result.known_outcome.returns}
                         if result.known_outcome else None,
        "turns": [gate.redact(t.to_log_dict()) for t in result.turns],
    }
    (EVIDENCE_DIR / "step_log.json").write_text(json.dumps(step_log, indent=2))

    if result.final_screenshot:
        (EVIDENCE_DIR / "final_screenshot.png").write_bytes(result.final_screenshot)

    if result.artifact:
        (EVIDENCE_DIR / "draft_artifact.json").write_text(
            result.artifact.model_dump_json(indent=2))

    print(f"stop_reason:  {result.stop_reason}")
    print(f"model_calls:  {result.model_calls}")
    print(f"turns:        {len(result.turns)}")
    for t in result.turns:
        print(f"  turn {t.turn}: {t.decided_action} -> {t.decided_target!r} "
              f"(rung={t.resolved_rung} strategy={t.resolved_strategy} ok={t.ok})"
              + (f"  note={t.note!r}" if t.note else ""))
    print(f"known_outcome: {result.known_outcome}")
    print(f"artifact produced: {result.artifact is not None}")
    print(f"evidence written to: {EVIDENCE_DIR}")

    if result.stop_reason != "business_outcome":
        raise SystemExit(
            f"expected a business_outcome (member not found), got "
            f"'{result.stop_reason}' instead -- not forcing a result, "
            f"see turns above for what actually happened")


if __name__ == "__main__":
    main()
