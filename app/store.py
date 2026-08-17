"""Versioned catalog + the discovery seam.

Discovery is where the real LLM-driven run plugs in. To keep the HTTP service
runnable with no keys, FakeDiscovery emits the known-good member-lookup
artifact directly. The real agent would produce the same shape from a live run.
"""
from __future__ import annotations

from .models import (ApprovalState, CapabilityArtifact, KnownOutcome, Locator,
                     Output, Param, Step)


class ArtifactStore:
    def __init__(self):
        self._by_name: dict[str, CapabilityArtifact] = {}

    def save(self, art: CapabilityArtifact) -> None:
        self._by_name[art.name] = art

    def get(self, name: str) -> CapabilityArtifact | None:
        return self._by_name.get(name)

    def list(self) -> list[CapabilityArtifact]:
        return list(self._by_name.values())


def fake_discovery(goal: str, target: str) -> CapabilityArtifact:
    """Stand-in for a real discovery run. Returns a DRAFT artifact."""
    return CapabilityArtifact(
        id="cap_lookup_savings",
        name="lookup_savings",
        description="Look up a member and read their savings balance.",
        target={"app_id": "fake-core-banking", "entry_point": target,
                "allowlist_ref": "core-banking"},
        inputs=[Param(name="member_id", type="string")],
        outputs=[Output(name="savings_balance", type="money",
                        source_step="s3")],
        steps=[
            Step(id="s1", action="type",
                 target=[Locator(strategy="role_name", value="search_box")],
                 value_from="input.member_id"),
            Step(id="s2", action="click",
                 target=[Locator(strategy="role_name", value="search_button")],
                 checkpoint="screen==detail"),
            Step(id="s3", action="read",
                 target=[Locator(strategy="label_anchor",
                                 value="savings_balance")],
                 checkpoint="field:savings_balance"),
        ],
        success="field:savings_balance",
        known_outcomes=[KnownOutcome(name="member_not_found",
                                     detect="screen==not_found",
                                     returns="not_found")],
        redaction=["input.ssn"],
        approval_state=ApprovalState.draft,
        provenance={"recorded_by": "fake_discovery", "goal": goal},
    )
