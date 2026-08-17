"""Proves redaction is real: a sensitive value that passes through
SafetyGate.redact() and gets written to disk leaves the mask in the file,
never the raw value -- whether it was caught by field name or by content
pattern, and however deeply nested.
"""
import json

from app.safety import SafetyGate

gate = SafetyGate(allowed_actions={"type", "click", "read", "wait", "navigate"},
                  redact_keys={"ssn", "password", "token"})

SSN = "123-45-6789"
CARD = "4111-1111-1111-1111"
ACCOUNT = "88293014477"


def test_redacted_log_never_contains_the_raw_sensitive_values(tmp_path):
    # One value caught by FIELD NAME (an innocuous key holding an SSN), and
    # two caught by CONTENT alone -- a card and an account number sitting
    # inside a free-text "note", the shape a driver's ActionResult.note
    # would actually take, under an unlabeled key regex has to catch on
    # its own.
    log = {
        "step": "s1",
        "ssn": SSN,
        "note": f"typed card {CARD} into the field",
        "nested": {"observed": f"account {ACCOUNT} on file"},
    }

    redacted = gate.redact(log)
    out_path = tmp_path / "step_log.json"
    out_path.write_text(json.dumps(redacted, indent=2))
    written = out_path.read_text()

    for raw in (SSN, CARD, ACCOUNT):
        assert raw not in written, f"raw sensitive value {raw!r} leaked into the log"
    assert written.count("<redacted>") == 3
