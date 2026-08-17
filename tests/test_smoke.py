"""Confirms the service boots and the basic HTTP surface responds, plus the
one load-bearing HTTP-layer rule: a draft capability cannot be invoked.
"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_discover_then_list():
    resp = client.post("/discover", json={"goal": "read savings balance",
                                          "target": "http://localhost:8080/core-banking"})
    assert resp.status_code == 200
    caps = client.get("/capabilities").json()
    assert any(c["name"] == "lookup_savings" for c in caps)


def test_draft_capability_cannot_be_invoked_until_approved():
    """/discover always returns a draft (see fake_discovery); invoking it
    must be refused until a separate /approve call -- an agent should never
    be able to run a capability a human hasn't signed off on.
    """
    client.post("/discover", json={"goal": "read savings balance",
                                    "target": "http://localhost:8080/core-banking"})

    resp = client.post("/capabilities/lookup_savings/invoke",
                       json={"inputs": {"member_id": "12345"}})
    assert resp.status_code == 409

    client.post("/capabilities/lookup_savings/approve")
    resp = client.post("/capabilities/lookup_savings/invoke",
                       json={"inputs": {"member_id": "12345"}})
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
