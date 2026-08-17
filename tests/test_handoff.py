"""Auto-escalation: a hard failure raises a handoff request carrying real
context; a business outcome is a valid answer and must never raise one.
"""
from app.driver import FakeBankDriver
from app.replay import replay
from app.safety import SafetyGate
from app.store import fake_discovery

gate = SafetyGate(allowed_actions={"type", "click", "read", "wait", "navigate"},
                  redact_keys={"ssn", "password", "token"})


def _artifact():
    return fake_discovery(goal="look up a member's savings balance",
                          target="http://localhost:8080/core-banking")


def test_hard_failure_auto_raises_handoff_but_business_outcome_does_not():
    raised = []

    def capture(context, screenshot):
        raised.append(context)

    replay(_artifact(), {"member_id": "12345"}, FakeBankDriver(hide_balance=True),
          gate, on_stuck=capture)

    assert len(raised) == 1
    assert raised[0]["step"] == "s3"
    assert "field:savings_balance" in raised[0]["reason"]

    raised.clear()
    replay(_artifact(), {"member_id": "99999"}, FakeBankDriver(), gate,
          on_stuck=capture)

    assert raised == []
