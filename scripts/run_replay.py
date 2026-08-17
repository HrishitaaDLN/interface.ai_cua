"""Runs the approved lookup_savings recipe through app.replay.replay() --
completely unmodified -- pointed at PlaywrightBankDriver against the live
legacy bank page. No LLM anywhere in this file or in replay.py.

Three real runs, each producing one of the three result contracts:
  1. member 12345                    -> success
  2. member 99999                    -> business_outcome (member_not_found)
  3. member 12345 + hide_balance=1   -> failure, naming the exact step

Every run uses the SAME driver class and the SAME locator ladders the
approved artifact recorded from discovery. Each step's resolved rung is
read back from driver.rung_log and saved alongside the result, so it can be
compared against what discovery recorded for the same steps.

Precondition: the legacy bank server must already be running:
    uvicorn legacy_bank.server:app --port 8080

Run:
    python scripts/run_replay.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.driver import PlaywrightBankDriver
from app.models import ApprovalState, CapabilityArtifact
from app.replay import replay
from app.safety import SafetyGate

ARTIFACT_PATH = ROOT / "evidence" / "approved-recipe" / "lookup_savings.json"

# Same allowlist/redact keys the live HTTP service (app/main.py) uses.
gate = SafetyGate(allowed_actions={"type", "click", "read", "wait", "navigate"},
                  redact_keys={"ssn", "password", "token"})


def _run(scenario: str, artifact: CapabilityArtifact, member_id: str,
         entry_url: str) -> None:
    out_dir = ROOT / "evidence" / f"replay-{scenario}"
    out_dir.mkdir(parents=True, exist_ok=True)

    with PlaywrightBankDriver(headless=True) as driver:
        # Bootstrap navigation, same role as main.py constructing a fresh
        # driver per request -- replay() itself never navigates, it assumes
        # the driver is already on the artifact's entry screen. hide_balance
        # rides along as a query param on THIS navigation only, the same
        # test-only injection point FakeBankDriver used a constructor flag
        # for; the recipe's steps know nothing about it either way.
        driver.act("navigate", value=entry_url)
        result = replay(artifact, {"member_id": member_id}, driver, gate)

        steps_executed = [
            {"step_id": step.id, "action": step.action,
             "resolved_rung": rung.get("rung"),
             "resolved_strategy": rung.get("strategy"),
             "ok": rung.get("ok")}
            for step, rung in zip(artifact.steps, driver.rung_log)
        ]
        step_log = gate.redact({
            "scenario": scenario,
            "capability": artifact.name,
            "inputs": {"member_id": member_id},
            "result": json.loads(result.model_dump_json()),
            "steps_executed": steps_executed,
        })
        (out_dir / "step_log.json").write_text(json.dumps(step_log, indent=2))

        if result.status == "failure":
            obs = driver.perceive()
            if obs.screenshot:
                (out_dir / "final_screenshot.png").write_bytes(obs.screenshot)

    print(f"\n--- {scenario} (member {member_id}) ---")
    print(result.model_dump_json(indent=2))
    for e in steps_executed:
        print(f"  {e['step_id']} ({e['action']}): rung={e['resolved_rung']} "
              f"strategy={e['resolved_strategy']} ok={e['ok']}")


def main() -> None:
    artifact = CapabilityArtifact.model_validate_json(ARTIFACT_PATH.read_text())
    if artifact.approval_state != ApprovalState.approved:
        raise SystemExit("refusing to replay a draft recipe")

    entry = artifact.target["entry_point"]
    _run("success", artifact, "12345", entry)
    _run("business-outcome", artifact, "99999", entry)
    _run("failure", artifact, "12345", f"{entry}?hide_balance=1")


if __name__ == "__main__":
    main()
