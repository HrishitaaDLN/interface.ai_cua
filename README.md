# Computer-Use Automation

An LLM drives a legacy web app once to discover how to do a task. That run
is compiled into a typed recipe (a "capability artifact"). From then on, a
plain deterministic engine replays the recipe with no LLM involved. See
`FINAL-DESIGN-AND-STACK.md` for the full design and `REPORT.md` for what was
actually built and tested.

## Setup

```bash
pip install -r requirements.txt
python -m playwright install chromium   # only needed for the real (non-keyless) path
```

## Demo path A: real run (live page, a real model, real replay)

Needs two things running:
- the legacy bank page, in its own terminal: `uvicorn legacy_bank.server:app --port 8080`
- a Gemini API key, read from an environment variable. Put it in a `.env`
  file at the repo root (gitignored, never logged or written to evidence):
  `GEMINI_API_KEY=your-key-here`

```bash
# (a) run the agent on a goal against the live page, produces a draft artifact
python scripts/run_discovery.py
#   -> evidence/discovery-run/draft_artifact.json (draft, success path,
#      member 12345)
python scripts/run_discovery_notfound.py
#   -> evidence/discovery-run-notfound/draft_artifact.json (draft, the
#      not-found path, member 99999)

# reconcile the two drafts into one approved recipe (plain Python, no model)
python scripts/merge_and_approve.py
#   -> evidence/approved-recipe/lookup_savings.json (approved)

# (b) replay the produced, approved artifact against the live page, no LLM
python scripts/run_replay.py
#   -> four real runs, each saved under evidence/replay-<scenario>/:
#      success, business_outcome, recoverable (bounded retry then success),
#      and hard failure (which also auto-raises a handoff request)

# (c) same-session human handoff: pauses on a live stuck state without
# closing the browser, a separate process fixes the page over CDP, replay
# resumes from the paused step (not from the top) and reaches success
python scripts/run_handoff_resume.py
#   -> evidence/handoff/before_handoff.json, human_action.json,
#      after_resume.json -- same CDP session id and same run id recorded
#      across all three, proving one continuous session, not three claims
```

Every command above was run fresh to verify this README, including the two
discovery runs (real Gemini calls), all four replay scenarios, and the
handoff/resume demo (all three real Playwright against the live page).

## Demo path B: without live services (keyless, no browser, no model)

A fake in-memory bank app stands in for the legacy surface, and a scripted
stand-in (`fake_discovery`) stands in for the agent, so the same
discover-then-replay shape works with nothing beyond fastapi/pydantic/uvicorn
installed.

```bash
uvicorn app.main:app --reload
# in another terminal:

# (a) run the agent (fake_discovery stands in) on a goal, produces a draft artifact
curl -X POST localhost:8000/discover \
  -H 'content-type: application/json' \
  -d '{"goal":"read savings balance","target":"http://localhost:8080/core-banking"}'

curl -X POST localhost:8000/capabilities/lookup_savings/approve

# (b) replay the produced, approved artifact
curl -X POST localhost:8000/capabilities/lookup_savings/invoke \
  -H 'content-type: application/json' -d '{"inputs":{"member_id":"12345"}}'
#   -> {"status":"success","outputs":{"savings_balance":"$4,210.55"}, ...}

curl -X POST localhost:8000/capabilities/lookup_savings/invoke \
  -H 'content-type: application/json' -d '{"inputs":{"member_id":"99999"}}'
#   -> {"status":"business_outcome","name":"member_not_found", ...}

curl -X POST localhost:8000/capabilities/lookup_savings/invoke \
  -H 'content-type: application/json' \
  -d '{"inputs":{"member_id":"12345"},"hide_balance":true}'
#   -> {"status":"failure","step":"s3","expected":"field:savings_balance", ...}
#      (this also auto-raises a handoff request, see below)

# handoff seam (manual request shown here; replay also auto-raises one on
# a hard failure, carrying capability/step/reason/a redacted snapshot)
curl -X POST localhost:8000/handoff/request \
  -H 'content-type: application/json' -d '{"reason":"unknown dialog"}'
curl -X POST localhost:8000/handoff/take
curl -X POST localhost:8000/handoff/release
```

Every command above was run fresh against a clean server process to verify
this README.

## What is real vs stubbed

- Real: the artifact schema, the deterministic replay engine, the three-way
  result contract with a bounded recoverable path in front of it, the safety
  gate (allowlist + field and content redaction), the handoff token state
  machine (auto-raised on a hard failure, not just manual), same-session
  pause and resume on a real stuck state (`evidence/handoff/`), the callable
  catalog.
- Stubbed at a clean seam: discovery (a real LLM-driven run plugs into
  `fake_discovery`) and the surface (`FakeBankDriver` stands in for the real
  `PlaywrightBankDriver`). Both sit behind interfaces so swapping them
  changes nothing above. The operator UI a human would use during a handoff
  is also stubbed: this demo has a human attach their own CDP tooling
  directly to the paused session instead.

In production, screenshots are redacted or withheld entirely (see
"Screenshots are the sneaky leak" in `FINAL-DESIGN-AND-STACK.md`). The one
screenshot tracked in this repo (`evidence/discovery-run/final_screenshot.png`)
is safe to commit only because it is fake demo data (Ada Lovelace, a made-up
balance).
