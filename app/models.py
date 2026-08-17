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
    redaction: list[str] = Field(default_factory=list)
    approval_state: ApprovalState = ApprovalState.draft
    provenance: dict[str, str] = Field(default_factory=dict)  # never the transcript


# ---- result contract -------------------------------------------------------
# Every run returns exactly ONE of these three. This is what stops a real
# business answer ("no such member") from being mistaken for a crash.

class Success(BaseModel):
    status: Literal["success"] = "success"
    outputs: dict[str, Any]
    run_id: str


class BusinessOutcome(BaseModel):
    status: Literal["business_outcome"] = "business_outcome"
    name: str
    run_id: str


class Failure(BaseModel):
    status: Literal["failure"] = "failure"
    step: str
    expected: str
    observed: str
    run_id: str


Result = Success | BusinessOutcome | Failure
