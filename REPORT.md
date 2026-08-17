# Report

## Architecture

The system is one Python process split by a clean seam, not a distributed system and not a framework.

The seam is the driver: two verbs, `perceive()` and `act()`. `perceive()` returns the current screen state (a screen name, a dict of readable fields, and for the real driver an accessibility tree and a screenshot). `act()` performs one action and reports whether it worked. Everything above the driver, the replay engine, the safety gate, the artifact schema, only talks to those two verbs.

Two drivers implement it. `FakeBankDriver` simulates the bank app in memory, no browser, used for fast tests. `PlaywrightBankDriver` drives the real legacy page at `http://localhost:8080` with Playwright. The proof the seam holds: `app/replay.py` was written once and never changed. It ran unmodified against `FakeBankDriver` in the keyless demo, and unmodified against `PlaywrightBankDriver` on the live page in step 5. Same function, two different surfaces underneath.

Discovery and replay are two modes on the same driver interface, not two systems. Discovery (`app/discovery.py`) is a live observe, decide, act loop driven by a vision model, which compiles what it did into a draft artifact. Replay (`app/replay.py`) walks an approved artifact's steps with no model involved. Discovery is rare and expensive; replay is what runs in production. The design keeps the model out of the replay path entirely.

A few decisions were made against real alternatives. One process instead of services: the brief penalizes premature scaling infrastructure, and nothing here needs independent scaling yet. A direct model call instead of an agent framework: this keeps full control of the stopping logic, and every line of that logic has to be defended, which a framework's internals make harder to do. An accessibility tree plus a screenshot instead of pure coordinates: coordinates alone break the moment the page has no clean DOM to anchor to, which is exactly the legacy case this system targets.

Observability here is the structured step logs plus the failure screenshot (see evidence/), not a separate metrics or tracing layer. That was a deliberate choice, not an oversight: the brief discourages premature infrastructure, and a metrics/tracing stack has nothing to attach to yet with a single process and no production traffic.

The FastAPI service (`app/main.py`) is the front door: the boundary between a calling agent and everything underneath it. An agent does not see the driver, the safety gate, or the replay engine directly; it sees a catalog of named, typed capabilities it discovers, approves, and invokes by name over HTTP. Everything below that catalog interface is hidden from the caller. That is what makes this a capability an agent calls, not a script a human runs by hand.

The live session also has exactly one holder at a time, agent, replay, or human, tracked by a control token. That single-holder rule is itself part of the architecture, not just a detail of the escalation mechanism: it is how the system always knows who is currently allowed to act. The Escalation & handoff section covers how the token moves.

Two persistence boundaries sit behind their own narrow interfaces as well: capabilities persist through an artifact store and run evidence persists through an evidence store. Today both are simple (an in-memory dict for the demo service's artifact store, plain JSON and screenshot files under evidence/ for run evidence), but the interface is what callers depend on, so either backend could change without touching the logic above.

## Artifact schema

The artifact (`CapabilityArtifact`, `app/models.py`) is a typed contract, not a click recording.

Inputs and outputs are typed and named. This is what let the same approved recipe run three times in step 5 with three different `member_id` values and produce three correct, different results.

Steps bind data with `value_from` (e.g. `"input.member_id"`), never a literal baked into the step. `Step` has no field for a raw literal, so there is no path by which a value typed during discovery ends up frozen into a recipe. That is redaction by schema design, not a filter that has to remember to catch it.

Each step carries a locator ladder: role and accessible name first, then an attribute selector, then a nearby-label anchor, then coordinates. Discovery and replay both walk it top to bottom and stop at the first rung that resolves. The three real replay runs needed three different rungs for three different controls on the same page, which is the actual argument for a ladder instead of one selector per step.

`known_outcomes` are first-class, not bolted onto error handling. A business result like "no such member" is declared the same way success is: a name, a detection checkpoint, a return value. The approved recipe (`evidence/approved-recipe/lookup_savings.json`) has one success path and one known outcome, both discovered from real runs, not invented.

`schema_version` and `approval_state` exist because a recipe is long-lived, not a one-off script. A draft cannot be invoked (tested). Only a human moving it to `approved` unlocks that. `provenance` records what produced a recipe but never the raw transcript.

## Determinism & error handling

Replay has no branch that depends on a model deciding at runtime. Every step resolves through the ladder or it does not, and the ladder logs which rung matched (`driver.rung_log`), which is a drift signal: a step that used to resolve on rung 1 and starts resolving on rung 3 means the page changed underneath a recipe that still technically works. In step 5, replay resolved the same three steps on the same rungs discovery did (rung 2, rung 3, rung 3), the concrete evidence the ladder is deterministic.

All waiting is condition-based: Playwright's own actionability waits and `wait_for_load_state()`, never `time.sleep()`. The page's injected 3-second slow-load delay was absorbed correctly with zero manual sleeps in the driver.

The three-way result contract (`Success`, `BusinessOutcome`, `Failure`) is the most load-bearing decision in the system, and all three were exercised for real:
- Member 12345 replayed to `success`, with `savings_balance: "$4,210.55"` actually present in `outputs`, not just a passing checkpoint.
- Member 99999 replayed to `business_outcome: member_not_found`, not a failure. `replay.py` checks `known_outcomes` before a step's own checkpoint, so the not-found branch and the happy-path checkpoint on the same click step never collide.
- Member 12345 with the page's hidden-balance switch on replayed to `failure`, naming the exact step (`d3`), what was expected (`field:savings_balance`), and what was observed (`screen=detail`). The ladder independently exhausted all three rungs on that step too, agreeing with the checkpoint.

Now that all three are built, the full taxonomy replay classifies anything into is: recoverable (a transient slow load or a dismissable interstitial, given a few bounded retries before anything is decided), business outcome (a real, valid answer that is not what was asked for, like member not found), and hard failure (a genuine stuck state, naming the exact step). A permission denial on the page would classify as a business outcome, since it is a real answer the system gave, not a crash. An unexpected dialog blocking the page would classify as recoverable, since it is exactly what known_interstitials exists to dismiss and retry past.

Two real bugs surfaced during discovery, worth stating plainly since they are the actual argument for testing against a live surface instead of trusting the design on paper.

First, an early loop checked the success checkpoint after every action, not just after an explicit read. Since `perceive()` extracts every visible field generically, the checkpoint went true as a side effect of the click alone, before the model ever decided to read anything. The compiled draft had a passing checkpoint and an empty `outputs` list, which would replay as "success" with no data. Fixed by only checking the checkpoint after an explicit `read`.

Second, the read step's ladder guessed a `role_name` locator using the resolved label text ("Savings Balance") as the name to search for. On this page a cell's accessible name is its own text, so the guess matched the label cell, not the value cell beside it. The run still reported success only because `perceive()` re-extracts the table independently of what the read step resolved, which masked the bug. The step log's `note` field caught it directly: it said `"Savings Balance"` instead of `"$4,210.55"`. Fixed by using the model's own raw, non-colliding phrasing for the early guesses.

Neither bug was caught by design review. Both were caught by running the real thing and reading the output closely.

## Heterogeneity & multi-tenant

Design only. Nothing beyond the web driver was built.

The `perceive()`/`act()` seam is the intended answer to different app types: a desktop driver (Windows UI Automation, or another OS's accessibility API) could implement the same two verbs and nothing above it would change, the same way replay.py did not change when the browser driver replaced the fake one. That claim is credible because it already happened once. It has not been tested with a second, structurally different surface.

For multi-tenant reuse, the intended shape is a shared logical flow with a per-tenant binding layered on top: the same recipe, but a different `target.entry_point`, `allowlist_ref`, and credentials per tenant, without duplicating the recipe. The schema already separates `target` from `steps`, which is what would make this possible, but no code resolves a per-tenant binding at invoke time.

Drift detection across tenants would use the same rung-logging mechanism already built: if tenant B's copy of the app needs rung 3 for a control that resolves on rung 1 for tenant A, that is a visible signal the two surfaces are not identical, without needing a second recipe.

## Escalation & handoff

The mechanism is real. The UI around it is not.

`app/main.py` implements a control-token state machine (`AGENT_CONTROL`, `HANDOFF_REQUESTED`, `HUMAN_CONTROL`) with three endpoints. When the system gets stuck it requests a handoff with a reason; a human takes control of the same live driver session, not a re-created copy of it; releasing hands it back.

What is mocked is everything a human would see: there is no dashboard showing a paused run or a takeover button. The state machine and endpoints are real and tested directly; the console a human would use is not built.

Resume is mostly a property of the schema: every step has `retry_safe`, meaning it can be safely re-run if a process picks back up mid-run. The control-token transfer itself is real and tested. Pausing a live in-flight Playwright run and resuming it on the same session is designed, not exercised: nothing in this project has actually demonstrated a same-session resume.

## Safety

Every action passes through one `SafetyGate` (`app/safety.py`) before it runs. Actions outside an explicit allowlist are denied. A short list (`submit`, `delete`, `confirm`, `transfer`) always needs approval regardless of the allowlist, since those carry real side effects.

Redaction goes through one function, `SafetyGate.redact()`, and every place that writes a log or evidence file in this project calls it, not a locally reinvented version. It masks two ways: by field name (anything in `redact_keys`, or in an artifact's own `redaction` list, has its entire value masked at any nesting depth) and by content (regex for SSNs, card numbers, and account-number-shaped digit runs, masked wherever they appear in a string, even under an unlabeled key like a free-text note). Tested directly: a value embedded in a nested `note` field was redacted, written to an actual file, and confirmed absent from the file while the mask was present.

As noted above, `value_from` binding means no literal a recipe types ever gets frozen into it. That is a consequence of the schema having no field to hold a literal, not a rule someone has to remember to apply.

Secrets are out of band. The model API key is read from an environment variable, loaded from a gitignored `.env` file, never logged or written into evidence. This was a real constraint, not theoretical: two different keys were used during discovery and neither was ever echoed into a shell command or a file outside `.env`.

Honest limits: the card and account regexes are reasonable heuristics, not a full PII scanner. They can miss unusual formats and could over-redact a long benign digit string. Screenshots are the sneakiest leak in this design; only one is tracked in this repo, from the discovery run, kept only because the data on it is fake. In production, screenshots should be redacted or withheld, which the README now says explicitly. The allowlist and redaction protect data and actions during replay, but discovery still has a model clicking freely on a live page. Risky actions are gated even during discovery, through the same `SafetyGate`, but discovery is meant to run against sandboxed or non-production instances only.

## Cuts

Built and run for real: the artifact schema, the deterministic replay engine, the three-way result contract, the safety gate, the locator ladder with rung logging, the real Playwright driver, one genuine discovery run to a success outcome and one to a business outcome, the merge of two discovery drafts into one approved recipe, and real replay runs against the live page covering all four states. The draft to approved lifecycle, the confidence and approval stretch goal, is also already implemented and enforced, not just designed: a draft cannot be invoked, and only a human moving it to approved unlocks that, tested directly.

Stubbed or design-only:

- A real desktop or non-web driver. The interface supports it, but nothing beyond the web driver was built or tested.
- Multi-tenant reuse. The schema has the right shape, but no per-tenant binding resolution exists.
- The operator dashboard for handoff. The state machine is real; the UI is not built.
- Retry and resume after a handoff. `retry_safe` exists on every step but nothing exercises an actual resume.
- Interstitial dismissal (a recognized dismissable overlay gets clicked through and the run continues) is built into replay.py the same way the slow-load retry is, but this project's page has no such overlay to exercise it against, so unlike the slow-load path it is untested by real evidence.
- Verifying a recipe by replaying it repeatedly proves little on a static local app, since nothing on the page changes between runs. The replay runs prove the mechanism works, not that the recipe survives a real app changing over time.

Next, in order: a second, structurally different driver, to actually test the heterogeneity claim instead of asserting it; a minimal operator view for handoff, even a plain page with pending requests and a takeover button, to make the mechanism demonstrable end to end; and a second discovery-to-approval cycle against a page that changes between runs, to see whether rung drift catches something real instead of only being exercised against a static one.
