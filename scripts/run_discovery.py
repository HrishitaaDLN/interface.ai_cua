"""Runs ONE real discovery run against the live legacy bank page and saves
evidence. No fake/simulated fallback: if GEMINI_API_KEY is missing, this
raises and stops rather than substituting anything.

Precondition: the legacy bank server must already be running:
    uvicorn legacy_bank.server:app --port 8080

Run:
    python scripts/run_discovery.py
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

GOAL = "look up member 12345 and read their savings balance"
ENTRY_POINT = "http://localhost:8080/core-banking"
EVIDENCE_DIR = ROOT / "evidence" / "discovery-run"

# Same allowlist/redact keys the live service (app/main.py) uses -- there's
# nothing SSN/card/account-shaped in this run's data, but every write still
# goes through the one redact() function on principle.
gate = SafetyGate(allowed_actions={"type", "click", "read", "wait", "navigate"},
                  redact_keys={"ssn", "password", "token"})


def main() -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    with PlaywrightBankDriver(headless=True) as driver:
        result = run_discovery(
            driver, goal=GOAL, entry_point=ENTRY_POINT,
            inputs={"member_id": "12345"},
            success_checkpoint="field:savings_balance",
        )

    step_log = {
        "goal": result.goal,
        "model": result.model_name,
        "model_calls": result.model_calls,
        "stop_reason": result.stop_reason,
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
              f"(rung={t.resolved_rung} strategy={t.resolved_strategy} ok={t.ok})")
    print(f"artifact produced: {result.artifact is not None}")
    print(f"evidence written to: {EVIDENCE_DIR}")

    if result.stop_reason != "success":
        raise SystemExit(f"discovery did not succeed: {result.stop_reason}")


if __name__ == "__main__":
    main()
