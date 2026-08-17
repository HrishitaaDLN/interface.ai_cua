"""HTTP surface. Exposes saved capabilities as a callable catalog plus the
discovery, approval, replay, and handoff paths.

Run:  uvicorn app.main:app --reload
Docs: http://localhost:8000/docs
"""
from __future__ import annotations

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

# in-memory handoff control token: one holder at a time
handoff = {"state": "AGENT_CONTROL", "context": None}


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
    return replay(art, req.inputs, driver, gate)


# ---- handoff seam ----------------------------------------------------------

@app.post("/handoff/request")
def handoff_request(reason: str = "stuck"):
    handoff.update(state="HANDOFF_REQUESTED", context={"reason": reason})
    return handoff


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
