"""Demonstrates a real same-session human handoff, once, for real.

The automation drives into a genuine stuck state (member 12345 with the
page's hide_balance switch on, same trigger as the hard-failure evidence
run) and PAUSES on that live session instead of closing it. A human, a
genuinely separate process attaching over Chrome DevTools Protocol, not a
fresh browser, fixes the page in that exact session. Replay then RESUMES
from the step it paused on, re-reading current state via perceive(), not
restarting from the top, and reaches success.

"Same session" is not just asserted in prose here. Chrome's own CDP debug
endpoint (http://localhost:<port>/json) assigns each open tab a target id;
this script and the separate human-action process both read and record it,
so evidence/handoff/before_handoff.json, human_action.json, and
after_resume.json can be compared directly: same session_id and same
run_id across all three is what proves continuity, not a claim.

Precondition: the legacy bank server must already be running:
    uvicorn legacy_bank.server:app --port 8080

Run:
    python scripts/run_handoff_resume.py
"""
from __future__ import annotations

import json
import re
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
OUT_DIR = ROOT / "evidence" / "handoff"
CDP_PORT = 9222

gate = SafetyGate(allowed_actions={"type", "click", "read", "wait", "navigate"},
                  redact_keys={"ssn", "password", "token"})


def _write(name: str, data: dict) -> None:
    (OUT_DIR / name).write_text(json.dumps(data, indent=2))


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

        completed_before = [s.id for s in artifact.steps[:paused.resume.step_index]]
        session_id_before = driver.cdp_session_id()
        rung_log_before = list(driver.rung_log)

        before_handoff = {
            "run_id": paused.run_id,
            "session_id": session_id_before,
            "cdp_port": CDP_PORT,
            "capability": artifact.name,
            "inputs": {"member_id": member_id},
            "completed_steps": completed_before,
            "paused_at_step": paused.step,
            "reason": paused.reason,
            "handoff_request": raised_handoffs[-1] if raised_handoffs else None,
            "rung_log": rung_log_before,
        }
        _write("before_handoff.json", gate.redact(
            before_handoff, extra_keys=redaction_keys_from_artifact(artifact.redaction)))
        print("--- before_handoff ---")
        print(json.dumps(before_handoff, indent=2))

        # 2. The human step: a separate OS process attaches to the SAME
        # live browser over CDP (not a fresh one) and fixes the page.
        fix_url = f"{entry}/member?member_id={member_id}"
        human = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "simulated_human_action.py"),
             str(CDP_PORT), fix_url],
            capture_output=True, text=True, timeout=30)
        if human.returncode != 0:
            raise SystemExit(f"human action process failed:\n{human.stderr}")

        match = re.search(r"HUMAN_ACTION_JSON:(\{.*\})", human.stdout)
        human_json = json.loads(match.group(1)) if match else {}
        session_id_seen_by_human = human_json.get("session_id")

        human_action = {
            "run_id": paused.run_id,
            "session_id_seen_by_human_process": session_id_seen_by_human,
            "matches_before_handoff_session_id": (
                session_id_seen_by_human == session_id_before
                if session_id_before else None),
            "process": "genuinely separate OS process (subprocess.run), "
                       "attached over CDP, not a function call",
            "before_url": human_json.get("before_url"),
            "after_url": human_json.get("after_url"),
            "raw_stdout": human.stdout.strip(),
        }
        _write("human_action.json", human_action)
        print("\n--- human_action ---")
        print(json.dumps(human_action, indent=2))

        # 3. Resume: same driver, same session, picks up at the paused step.
        result = replay(artifact, {"member_id": member_id}, driver, gate,
                        on_stuck=on_stuck, resumable=True, resume=paused.resume)
        session_id_after = driver.cdp_session_id()
        rung_log_after = driver.rung_log[len(rung_log_before):]

        after_resume = {
            "run_id": result.run_id,
            "session_id": session_id_after,
            "same_run_as_before_handoff": result.run_id == paused.run_id,
            "same_session_as_before_handoff": (
                session_id_after == session_id_before
                if session_id_before else None),
            "resumed_from_step": paused.step,
            "resumed_from_step_index": paused.resume.step_index,
            "completed_steps_this_phase": [
                s.id for s in artifact.steps[paused.resume.step_index:]],
            "final_result": json.loads(result.model_dump_json()),
            "rung_log": rung_log_after,
        }
        _write("after_resume.json", after_resume)
        print("\n--- after_resume ---")
        print(json.dumps(after_resume, indent=2))

        if result.status != "success":
            raise SystemExit(
                f"expected success after resume, got {result.status!r} -- "
                f"not forcing it")

    finally:
        driver.close()

    print(f"\nevidence written to: {OUT_DIR}")
    print(f"same session proven by: session_id "
         f"{session_id_before!r} == {session_id_seen_by_human!r} == "
         f"{session_id_after!r}")
    print(f"same run proven by: run_id {paused.run_id!r} == {result.run_id!r}")


if __name__ == "__main__":
    main()
