"""The replay engine, against FakeBankDriver -- fast, no browser, no LLM.

Covers the three-way result contract: a real business answer must never be
mistaken for a crash, and a crash must name exactly where it happened.
"""
from app.driver import FakeBankDriver
from app.models import BusinessOutcome, Failure, Success
from app.replay import replay
from app.safety import SafetyGate
from app.store import fake_discovery

gate = SafetyGate(allowed_actions={"type", "click", "read", "wait", "navigate"},
                  redact_keys={"ssn", "password", "token"})


def _artifact():
    return fake_discovery(goal="look up a member's savings balance",
                          target="http://localhost:8080/core-banking")


def test_replay_success_returns_outputs():
    """A known member replays to success, with the balance in outputs --
    not just a passing checkpoint, the actual data the caller asked for."""
    result = replay(_artifact(), {"member_id": "12345"}, FakeBankDriver(), gate)

    assert isinstance(result, Success)
    assert result.outputs == {"savings_balance": "$4,210.55"}


def test_replay_unknown_member_is_business_outcome_not_failure():
    """An unknown member is a real answer ('no such member'), and must come
    back as business_outcome, never as failure -- that distinction is the
    whole point of the three-way contract."""
    result = replay(_artifact(), {"member_id": "99999"}, FakeBankDriver(), gate)

    assert isinstance(result, BusinessOutcome)
    assert result.name == "member_not_found"


def test_replay_hidden_balance_is_failure_naming_the_step():
    """When the page breaks (balance field missing), replay must fail
    loudly and name exactly which step and what was expected -- not return
    a success with a missing/null value."""
    result = replay(_artifact(), {"member_id": "12345"},
                    FakeBankDriver(hide_balance=True), gate)

    assert isinstance(result, Failure)
    assert result.step == "s3"
    assert result.expected == "field:savings_balance"
