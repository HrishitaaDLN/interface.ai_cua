"""HTTP surface. Exposes saved capabilities as a callable catalog plus the
discovery, approval, replay, and handoff paths.

Run:  uvicorn app.main:app --reload
Docs: http://localhost:8000/docs
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .driver import FakeBankDriver
from .models import ApprovalState, CapabilityArtifact
from .replay import replay
from .safety import SafetyGate
from .store import ArtifactStore, fake_discovery

app = FastAPI(title="Computer-Use Automation")
store = ArtifactStore()
gate = SafetyGate(allowed_actions={"type", "click", "read", "wait", "navigate"},
                  redact_keys={"ssn", "password", "token"})

HANDOFF_EVIDENCE_DIR = Path(__file__).resolve().parent.parent / "evidence" / "handoff"

# in-memory handoff control token: one holder at a time
handoff = {"state": "AGENT_CONTROL", "context": None}


def _raise_handoff(context: dict) -> dict:
    """The one place a handoff request actually gets created, whether a
    human called /handoff/request or replay auto-escalated on a hard
    failure. A new request replaces any existing pending one.
    """
    handoff["state"] = "HANDOFF_REQUESTED"
    handoff["context"] = context
    return handoff


def _on_replay_stuck(context: dict, screenshot: Optional[bytes]) -> None:
    """Wired into replay() as on_stuck: called only when replay is about to
    return a Failure. context is already redacted by replay.py before this
    runs. If a screenshot came with it, save it and record the path -- the
    driver hands back raw bytes, not a path, so saving is this layer's job,
    same as the replay evidence scripts already do on a failure.
    """
    if screenshot:
        HANDOFF_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        path = HANDOFF_EVIDENCE_DIR / f"{uuid.uuid4().hex[:8]}.png"
        path.write_bytes(screenshot)
        context["snapshot"]["screenshot_path"] = str(path)
    else:
        context["snapshot"]["screenshot_path"] = None
    _raise_handoff(context)


class DiscoverReq(BaseModel):
    goal: str
    target: str


class InvokeReq(BaseModel):
    inputs: dict = {}
    hide_balance: bool = False   # test hook to force a hard failure


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/discover", response_model=CapabilityArtifact)
def discover(req: DiscoverReq):
    art = fake_discovery(req.goal, req.target)
    store.save(art)
    return art


@app.get("/capabilities")
def list_caps():
    return [{"name": a.name, "description": a.description,
             "inputs": [p.name for p in a.inputs],
             "approval_state": a.approval_state} for a in store.list()]


@app.get("/capabilities/{name}", response_model=CapabilityArtifact)
def get_cap(name: str):
    art = store.get(name)
    if not art:
        raise HTTPException(404, "no such capability")
    return art


@app.post("/capabilities/{name}/approve")
def approve(name: str):
    art = store.get(name)
    if not art:
        raise HTTPException(404, "no such capability")
    art.approval_state = ApprovalState.approved
    store.save(art)
    return {"name": name, "approval_state": art.approval_state}


@app.post("/capabilities/{name}/invoke")
def invoke(name: str, req: InvokeReq):
    art = store.get(name)
    if not art:
        raise HTTPException(404, "no such capability")
    if art.approval_state != ApprovalState.approved:
        raise HTTPException(409, "capability is draft; approve before invoking")
    driver = FakeBankDriver(hide_balance=req.hide_balance)
    return replay(art, req.inputs, driver, gate, on_stuck=_on_replay_stuck)


# ---- handoff seam ----------------------------------------------------------
# take/release are unchanged. request is enriched: it now carries real
# context (capability, step, reason, a redacted state snapshot) instead of
# just a reason string, and can be raised by a human OR by replay itself.

class HandoffRequest(BaseModel):
    reason: str
    capability: Optional[str] = None
    step: Optional[str] = None
    snapshot: Optional[dict] = None


@app.post("/handoff/request")
def handoff_request(req: HandoffRequest):
    return _raise_handoff(req.model_dump())


@app.post("/handoff/take")
def handoff_take():
    if handoff["state"] != "HANDOFF_REQUESTED":
        raise HTTPException(409, "no pending handoff")
    handoff["state"] = "HUMAN_CONTROL"
    return handoff


@app.post("/handoff/release")
def handoff_release():
    handoff["state"] = "AGENT_CONTROL"
    return handoff
