# Computer-Use Automation System — Final Design and Tech Stack

A plain-language reference. Anyone should be able to read this top to bottom and
understand what the system is, how it works, what it is built with, and why each
choice was made.

---

## 1. What this system is

Banks and credit unions run old internal apps that have no API. The only way in
is to use the screen the way a human does: click, type, read. This system gives
an AI agent "hands" to operate those apps.

The core idea, in one sentence:

> An LLM figures out how to do a task on the app **once**, that run is saved as a
> typed **recipe**, and from then on a plain program **replays** the recipe with
> no LLM involved.

Discovery (with the AI) happens rarely. Replay (no AI) happens constantly, and
is fast, cheap, and repeatable. That split is the whole point.

---

## 2. How it works, end to end

1. **A request comes in.** Either a person supplies a goal and a target app, or a
   calling agent asks for a saved capability by name with inputs.
2. **If a recipe already exists** for that task, the system replays it with the
   given inputs and returns the result. No AI.
3. **If no recipe exists**, the AI runs a live discovery run: it looks at the
   screen, decides one action, the system performs it, and it repeats until the
   goal is reached. The successful run is compiled into a draft recipe.
4. **A human approves** the draft. Only approved recipes can run on their own.
5. **In production**, the recipe is called like a function, replayed
   deterministically, and returns one of three clearly separated results.

The AI and the system are two different things. The system is all the plain code
(clicking, replaying, checking, logging). The AI is the temporary brain, present
only during discovery. Replay has no brain.

---

## 3. The five pieces built for real

Everything else is described as a clean seam. These five are the working core.

1. **Driver vs recipe seam.** The "driver" is how we see and act on a surface,
   with two verbs: `perceive()` (returns the screen state) and `act()` (does one
   action). A web driver implements it now; a desktop driver could later, without
   changing any recipe. This seam is the answer to supporting different app types.

2. **Locator ladder.** Old apps have messy screens with no reliable IDs. So each
   step stores several ways to find a control, best first: accessibility role and
   visible name, then a stable attribute, then a nearby-label anchor, then screen
   coordinates as a last resort. Replay tries them top down. A backup succeeding
   where the top choice failed is an early warning that the app changed.

3. **Three-way result contract.** Every run returns exactly one of:
   - `success` with the requested outputs,
   - `business_outcome` (a real answer like "no such member", not a crash),
   - `failure` naming the exact step, what was expected, and what was seen.
   Keeping a real business answer separate from a crash is the single most
   important correctness decision.

4. **Human handoff.** When the system gets stuck, a control token moves from the
   agent to a human, who operates the same live session, fixes the step, and
   hands control back. The mechanism is real; the operator dashboard is mocked.

5. **Safety: allowlist and redaction.** Every action is checked against an
   allowlist before it runs. Risky, irreversible actions require approval.
   Sensitive data is masked before anything is written to a log or evidence.

---

## 4. The recipe (artifact) schema

The recipe is a typed contract, not a click log, so both a human and a calling
agent know exactly what it needs and returns.

```
CapabilityArtifact
  schema_version                       # so old recipes still load
  id, name, description                # human + agent readable
  target: { app_id, entry_point, allowlist_ref }
  inputs:  [ { name, type, required, sensitive } ]
  outputs: [ { name, type, source_step } ]
  steps:
    - id, action                       # type / click / read / navigate / wait
      target: [locator ladder]         # ordered fallbacks
      value_from: "input.member_id"    # binds a named input, not a literal
      checkpoint                       # asserted after the step lands
      retry_safe                       # safe to re-run on resume
  success                              # the overall goal condition
  known_outcomes: [ { name, detect, returns } ]   # business answers
  redaction: [ ...sensitive fields ]
  approval_state                       # draft | approved
  provenance                           # who/when recorded (never the transcript)
```

Why it is shaped this way: typed inputs and outputs give a clear function
signature; `value_from` binding is what lets one recipe run with different data;
business outcomes are first-class so they are never mistaken for errors;
versioning is cheap now and painful to add later.

---

## 5. How masking keeps bank data safe

The rule: raw sensitive data lives only in memory during a run and is never
written anywhere.

- **Redact by default, keep by allowlist.** For regulated data you mark what is
  safe to keep, not what to hide, so nothing slips through.
- **Answer live, mask at rest.** The real balance goes back in the response to
  the caller, but the saved log stores it as `<redacted>` or a shape.
- **One redaction function on every write.** All logs and evidence pass through a
  single masking step keyed on sensitive field names plus regexes for SSNs, card
  and account numbers. One place to get right and audit.
- **Screenshots are the sneaky leak.** A screenshot of a member page shows
  everything. Prefer an accessibility snapshot you control, or blur sensitive
  regions, or encrypt with short retention. For the demo, use fake data.
- **Secrets never enter the flow.** Credentials and tokens are handled out of
  band from environment variables, never in the recipe, logs, or repo.

---

## 6. Tech stack, with reasons

| Choice | Tool | Why |
|---|---|---|
| Language | **Python** | Strongest fit, best automation and model libraries. |
| API | **FastAPI** | Typed request bodies, auto docs, fast to build. |
| Schema / validation | **Pydantic v2** | The recipe is a typed contract; Pydantic enforces and serializes it. |
| Browser automation | **Playwright** | One tool for DOM, accessibility tree, and screenshots; supports a persistent session a human can attach to; real condition-based waiting. |
| LLM (discovery only) | **A vision-capable model, called directly** | Discovery reads screenshots. A small explicit loop is easier to control and defend than a framework. |
| Recipe storage | **JSON files or SQLite** | Plain and local; the interface matters, not the backend. |
| Redaction | **Field-based `redact()` + regex** | Deterministic and auditable for regulated data. |
| Testing | **pytest** | Focused on the load-bearing parts: replay and error handling. |
| Optional chat UI | **Thin React or one HTML page** | A control panel over the API, not the main event. |

**Deliberately not used: LangChain / agent frameworks.** The valuable parts here
(schema, deterministic replay, error taxonomy, safety, handoff) are custom logic
no framework provides, and the brief does not reward framework name-dropping. Its
built-in PII scanner is also the wrong tool: we already know which fields are
sensitive, so exact field-based masking beats a probabilistic scan.

---

## 7. What is built vs designed

- **Built and runnable (no keys, no browser needed):** the schema, the
  deterministic replay engine, the three-way result contract, the safety gate,
  the handoff token state machine, and the callable HTTP catalog. A fake
  in-memory bank app stands in so the full loop runs.
- **Stubbed at a clean seam, described in the write-up:** the real Playwright
  driver, one genuine LLM discovery run (required for the final submission), the
  multi-tenant reuse story (shared logical flow + per-tenant binding), and the
  approval/drift lifecycle.

---

## 8. Demo

One prompt, then three replays:

- Goal: "Look up member 12345 and read their savings balance."
- Replay member 12345 -> `success` with the balance.
- Replay member 99999 -> `business_outcome: member_not_found`.
- Replay with the balance field hidden -> `failure` naming the exact step.

That single sequence demonstrates discovery, the artifact, deterministic replay,
a business outcome, and a hard failure.

---

## 9. Honest limits

- The discovery run (AI driving the screen) is unreliable by nature. The design's
  whole job is to not depend on it in production, which is why replay exists.
- Verifying a recipe by replaying it several times proves little on a static
  local app, since nothing changes. Real confidence needs variation not simulated
  here.
- UI automation of legacy apps has a lower reliability ceiling than an API. This
  approach is the fallback for apps that offer no other way in, not a first choice.
