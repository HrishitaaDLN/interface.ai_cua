"""Confirms the service boots and the basic HTTP surface responds.

This is the step-1 sanity check, not the real test suite (that lands in
step 6 once replay has real scenarios to exercise).
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
