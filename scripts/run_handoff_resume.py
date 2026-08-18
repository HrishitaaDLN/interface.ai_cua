"""Demonstrates a real same-session human handoff, once, for real.

The automation drives into a genuine stuck state (member 12345 with the
page's hide_balance switch on, same trigger as the hard-failure evidence
run) and PAUSES on that live session instead of closing it. A human, a
genuinely separate process attaching over Chrome DevTools Protocol, not a
fresh browser, fixes the page in that exact session. Replay then RESUMES
from the step it paused on, re-reading current state via perceive(), not
restarting from the top, and reaches success.

Precondition: the legacy bank server must already be running:
    uvicorn legacy_bank.server:app --port 8080

Run:
    python scripts/run_handoff_resume.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.driver import PlaywrightBankDriver
from app.models import ApprovalState, CapabilityArtifact, Paused
from app.replay import replay
from app.safety import SafetyGate, redaction_keys_from_artifact

ARTIFACT_PATH = ROOT / "evidence" / "approved-recipe" / "lookup_savings.json"
OUT_DIR = ROOT / "evidence" / "handoff-resume"
CDP_PORT = 9222

gate = SafetyGate(allowed_actions={"type", "click", "read", "wait", "navigate"},
                  redact_keys={"ssn", "password", "token"})


def main() -> None:
    artifact = CapabilityArtifact.model_validate_json(ARTIFACT_PATH.read_text())
    if artifact.approval_state != ApprovalState.approved:
        raise SystemExit("refusing to replay a draft recipe")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entry = artifact.target["entry_point"]
    member_id = "12345"

    raised_handoffs: list[dict] = []

    def on_stuck(context: dict, screenshot) -> None:
        if screenshot:
            path = OUT_DIR / "pause_screenshot.png"
            path.write_bytes(screenshot)
            context["snapshot"]["screenshot_path"] = str(path.relative_to(ROOT))
        else:
            context["snapshot"]["screenshot_path"] = None
        raised_handoffs.append(context)

    driver = PlaywrightBankDriver(headless=True, remote_debug_port=CDP_PORT)
    log: dict = {"capability": artifact.name, "inputs": {"member_id": member_id}}

    try:
        # 1. Drive into the stuck state and pause on it, same session.
        driver.act("navigate", value=f"{entry}?hide_balance=1")
        paused = replay(artifact, {"member_id": member_id}, driver, gate,
                        on_stuck=on_stuck, resumable=True)

        if not isinstance(paused, Paused):
            raise SystemExit(
                f"expected a pause, got status="
                f"{getattr(paused, 'status', '?')!r} instead -- not forcing "
                f"a resume demo on a run that did not actually pause")

        log["pause"] = json.loads(paused.model_dump_json())
        log["handoff_request"] = raised_handoffs[-1] if raised_handoffs else None
        print("--- paused ---")
        print(paused.model_dump_json(indent=2))

        # 2. The human step: a separate OS process attaches to the SAME
        # live browser over CDP (not a fresh one) and fixes the page.
        fix_url = f"{entry}/member?member_id={member_id}"
        human = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "simulated_human_action.py"),
             str(CDP_PORT), fix_url],
            capture_output=True, text=True, timeout=30)
        print("\n--- human action (separate process, over CDP) ---")
        print(human.stdout.strip())
        if human.returncode != 0:
            raise SystemExit(f"human action process failed:\n{human.stderr}")
        log["human_action"] = {
            "note": ("a separate process attached over CDP to the paused "
                    "session and navigated the same live tab to the "
                    "detail page without hide_balance"),
            "process_output": human.stdout.strip(),
        }

        # 3. Resume: same driver, same session, picks up at the paused step.
        result = replay(artifact, {"member_id": member_id}, driver, gate,
                        on_stuck=on_stuck, resumable=True, resume=paused.resume)
        log["resume_result"] = json.loads(result.model_dump_json())
        log["rung_log_full_run"] = driver.rung_log
        print("\n--- resumed ---")
        print(result.model_dump_json(indent=2))

        if result.status != "success":
            raise SystemExit(
                f"expected success after resume, got {result.status!r} -- "
                f"not forcing it")

    finally:
        driver.close()

    step_log = gate.redact(log, extra_keys=redaction_keys_from_artifact(artifact.redaction))
    (OUT_DIR / "step_log.json").write_text(json.dumps(step_log, indent=2))
    print(f"\nevidence written to: {OUT_DIR}")


if __name__ == "__main__":
    main()
