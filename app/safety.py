"""Single choke point. Every action is checked before it runs, and every
log line is redacted. Neither discovery nor replay can bypass this.
"""
from __future__ import annotations

RISKY_ACTIONS = {"submit", "delete", "confirm", "transfer"}


class SafetyGate:
    def __init__(self, allowed_actions: set[str], redact_keys: set[str]):
        self.allowed_actions = allowed_actions
        self.redact_keys = redact_keys

    def check(self, action: str) -> str:
        """Returns 'allow', 'deny', or 'needs_approval'."""
        if action in RISKY_ACTIONS:
            return "needs_approval"
        if action not in self.allowed_actions:
            return "deny"
        return "allow"

    def redact(self, data: dict) -> dict:
        return {k: ("<redacted>" if k in self.redact_keys else v)
                for k, v in data.items()}
