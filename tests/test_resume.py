"""Pause/resume: a paused run continues from the exact step it stopped on,
using the same driver session, not from the start.
"""
from app.driver import FakeBankDriver
from app.models import Paused, Success
from app.replay import replay
from app.safety import SafetyGate
from app.store import fake_discovery

gate = SafetyGate(allowed_actions={"type", "click", "read", "wait", "navigate"},
                  redact_keys={"ssn", "password", "token"})


def _artifact():
    return fake_discovery(goal="look up a member's savings balance",
                          target="http://localhost:8080/core-banking")


def test_resume_continues_from_paused_step_not_from_start():
    driver = FakeBankDriver(hide_balance=True)  # step s3's checkpoint will fail

    paused = replay(_artifact(), {"member_id": "12345"}, driver, gate,
                    resumable=True)

    assert isinstance(paused, Paused)
    assert paused.step == "s3"
    assert paused.resume.step_index == 2  # s1, s2 already done; s3 is index 2

    calls_before_resume = list(driver.calls)
    assert calls_before_resume.count("type:search_box") == 1
    assert calls_before_resume.count("click:search_button") == 1

    # The human's fix: something external to replay changes the session.
    # Here that's flipping the same flag a real page's hide_balance query
    # param controls; the point is replay never redid the search itself.
    driver.hide_balance = False

    result = replay(_artifact(), {"member_id": "12345"}, driver, gate,
                    resumable=True, resume=paused.resume)

    assert isinstance(result, Success)
    assert result.outputs == {"savings_balance": "$4,210.55"}

    # s1 (type) and s2 (click) must not have been re-run on resume.
    assert driver.calls.count("type:search_box") == 1
    assert driver.calls.count("click:search_button") == 1
    # s3 (read) is retry_safe, so resuming re-runs it once more; harmless
    # since it has no side effects, and proves resume picked up at s3.
    assert driver.calls.count("read:savings_balance") >= 1
