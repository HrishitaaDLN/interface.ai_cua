"""Typed contracts: the capability artifact and the replay result.

These two shapes are the heart of the system. The artifact is what an agent
calls; the result contract is what it gets back. Everything else is plumbing.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ---- artifact schema -------------------------------------------------------

class Locator(BaseModel):
    """One way to find a control. Steps carry a list of these, best first."""
    strategy: Literal["role_name", "attribute", "label_anchor", "coordinates"]
    value: str


class Param(BaseModel):
    name: str
    type: str
    required: bool = True
    sensitive: bool = False


class Output(BaseModel):
    name: str
    type: str
    source_step: str


class Step(BaseModel):
    id: str
    action: Literal["type", "click", "read", "navigate", "wait"]
    # ordered fallbacks; replay tries them top-down
    target: list[Locator] = Field(default_factory=list)
    value_from: Optional[str] = None          # e.g. "input.member_id"
    checkpoint: Optional[str] = None          # asserted after the step lands
    retry_safe: bool = True                   # safe to re-run on resume


class KnownOutcome(BaseModel):
    name: str            # e.g. "member_not_found"
    detect: str          # signal on the surface that means this outcome
    returns: str         # status name handed back to the caller


class Interstitial(BaseModel):
    """A recognized dismissable overlay (e.g. a cookie banner or a stray
    dialog), not a business outcome and not a failure. replay.py dismisses
    it and keeps going, bounded to a small number of times per run.
    """
    name: str            # e.g. "session_dialog"
    detect: str          # same checkpoint syntax as KnownOutcome.detect
    dismiss: list[Locator] = Field(default_factory=list)
    dismiss_action: str = "click"


class ApprovalState(str, Enum):
    draft = "draft"
    approved = "approved"


class CapabilityArtifact(BaseModel):
    schema_version: str = "1.0.0"
    id: str
    name: str
    description: str
    target: dict[str, str]                    # app_id, entry_point, allowlist_ref
    inputs: list[Param] = Field(default_factory=list)
    outputs: list[Output] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    success: str                              # the goal checkpoint
    known_outcomes: list[KnownOutcome] = Field(default_factory=list)
    known_interstitials: list[Interstitial] = Field(default_factory=list)
    redaction: list[str] = Field(default_factory=list)
    approval_state: ApprovalState = ApprovalState.draft
    provenance: dict[str, str] = Field(default_factory=dict)  # never the transcript


# ---- result contract -------------------------------------------------------
# Every run returns exactly ONE of these three. This is what stops a real
# business answer ("no such member") from being mistaken for a crash.
#
# "Recoverable" (a transient slow load, a dismissed interstitial) is NOT a
# fourth status. It is a mid-run condition replay.py handles inline, within
# a small bounded number of retries, before deciding one of the three
# outcomes below. `recovered` is the visible record that it happened, kept
# on whichever outcome the run actually ended in.

class RecoveredEvent(BaseModel):
    step: str             # step id this happened during, or "success"
    condition: str        # "slow_load" or "interstitial:<name>"
    attempts: int         # bounded; never open-ended


class Success(BaseModel):
    status: Literal["success"] = "success"
    outputs: dict[str, Any]
    run_id: str
    recovered: list[RecoveredEvent] = Field(default_factory=list)


class BusinessOutcome(BaseModel):
    status: Literal["business_outcome"] = "business_outcome"
    name: str
    run_id: str
    recovered: list[RecoveredEvent] = Field(default_factory=list)


class Failure(BaseModel):
    status: Literal["failure"] = "failure"
    step: str
    expected: str
    observed: str
    run_id: str
    recovered: list[RecoveredEvent] = Field(default_factory=list)


Result = Success | BusinessOutcome | Failure
