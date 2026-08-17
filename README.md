# Computer-Use Automation — HTTP service

Exposes saved capabilities as a callable catalog. Runs with **no LLM and no
browser**: a fake in-memory bank app stands in for the legacy surface, so the
whole discovery -> approve -> invoke -> replay loop works keyless.

## Run

```bash
pip install fastapi "pydantic>=2" uvicorn
uvicorn app.main:app --reload
# open http://localhost:8000/docs
```

## Demo path

```bash
# 1. discover a capability (draft)
curl -X POST localhost:8000/discover \
  -H 'content-type: application/json' \
  -d '{"goal":"read savings balance","target":"http://localhost:8080/core-banking"}'

# 2. approve it (draft -> approved; invoking a draft is refused)
curl -X POST localhost:8000/capabilities/lookup_savings/approve

# 3a. success
curl -X POST localhost:8000/capabilities/lookup_savings/invoke \
  -H 'content-type: application/json' -d '{"inputs":{"member_id":"12345"}}'
#   -> {"status":"success","outputs":{"savings_balance":"$4,210.55"}, ...}

# 3b. business outcome (not a crash)
curl -X POST localhost:8000/capabilities/lookup_savings/invoke \
  -H 'content-type: application/json' -d '{"inputs":{"member_id":"99999"}}'
#   -> {"status":"business_outcome","name":"member_not_found", ...}

# 3c. hard failure (balance hidden to simulate a broken page)
curl -X POST localhost:8000/capabilities/lookup_savings/invoke \
  -H 'content-type: application/json' \
  -d '{"inputs":{"member_id":"12345"},"hide_balance":true}'
#   -> {"status":"failure","step":"s3","expected":"field:savings_balance", ...}

# handoff seam (manual request shown here; replay also auto-raises one on
# a hard failure, with capability/step/reason/snapshot already filled in)
curl -X POST localhost:8000/handoff/request \
  -H 'content-type: application/json' -d '{"reason":"unknown dialog"}'
curl -X POST localhost:8000/handoff/take
curl -X POST localhost:8000/handoff/release
```

## What is real vs stubbed

- Real: the artifact schema, the deterministic replay engine, the three-way
  result contract, the safety gate (allowlist + redaction), the handoff token
  state machine, the callable catalog.
- Stubbed at a clean seam: discovery (a real LLM-driven run plugs into
  `fake_discovery`) and the surface (`FakeBankDriver` stands in for a Playwright
  `WebDriver`). Both sit behind interfaces so swapping them changes nothing above.

In production, screenshots are redacted or withheld entirely (see "Screenshots
are the sneaky leak" in FINAL-DESIGN-AND-STACK.md) — the one screenshot tracked
in this repo (`evidence/discovery-run/final_screenshot.png`) is safe to commit
only because it's fake demo data (Ada Lovelace, a made-up balance).
