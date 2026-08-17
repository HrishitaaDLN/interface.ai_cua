"""Single choke point. Every action is checked before it runs, and every
log or evidence write is redacted through this one redact() function.
Neither discovery nor replay can bypass this.
"""
from __future__ import annotations

import re

RISKY_ACTIONS = {"submit", "delete", "confirm", "transfer"}

MASK = "<redacted>"

# Content-based masking: these run on every string value, regardless of
# what key it sits under, so a sensitive value typed into an innocuously-
# named field (or embedded in a free-text "note") still gets caught.
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CARD_RE = re.compile(r"\b\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}\b|\b\d{16}\b")
# 8-17 consecutive digits: covers common bank account number lengths.
# Deliberately above this demo's 5-digit member ids, so business data
# (member_id, the balance) stays legible while real account-shaped numbers
# get masked.
_ACCOUNT_RE = re.compile(r"\b\d{8,17}\b")
_CONTENT_PATTERNS = (_SSN_RE, _CARD_RE, _ACCOUNT_RE)


class SafetyGate:
    def __init__(self, allowed_actions: set[str], redact_keys: set[str]):
        self.allowed_actions = allowed_actions
        self.redact_keys = {k.lower() for k in redact_keys}

    def check(self, action: str) -> str:
        """Returns 'allow', 'deny', or 'needs_approval'."""
        if action in RISKY_ACTIONS:
            return "needs_approval"
        if action not in self.allowed_actions:
            return "deny"
        return "allow"

    def redact(self, data, extra_keys: set[str] = frozenset()):
        """Recursively masks `data` two ways: (a) by field name -- any key
        in redact_keys (from this gate) or extra_keys (e.g. an artifact's
        own `redaction` list) has its ENTIRE value masked, however deeply
        nested or shaped; (b) by content -- SSN/card/account-shaped
        substrings are masked wherever they appear in a string value, even
        under an unlabeled key. Recurses through dicts and lists; anything
        else (numbers, bools, None) passes through unchanged.
        """
        keys = self.redact_keys | {k.lower() for k in extra_keys}
        return self._walk(data, keys)

    def _walk(self, value, keys: set[str]):
        if isinstance(value, dict):
            return {k: (MASK if k.lower() in keys else self._walk(v, keys))
                    for k, v in value.items()}
        if isinstance(value, list):
            return [self._walk(v, keys) for v in value]
        if isinstance(value, str):
            return self._mask_content(value)
        return value

    @staticmethod
    def _mask_content(text: str) -> str:
        for pattern in _CONTENT_PATTERNS:
            text = pattern.sub(MASK, text)
        return text


def redaction_keys_from_artifact(redaction_paths: list[str]) -> set[str]:
    """An artifact's `redaction` list holds dotted paths like "input.ssn";
    redact() matches by bare key name, so take each path's last segment.
    """
    return {p.rsplit(".", 1)[-1] for p in redaction_paths}
