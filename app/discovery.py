"""Real discovery: observe -> decide -> act, driven by a vision-capable model.

This is the ONLY module in the system where an LLM touches anything. Each
turn it calls perceive() on the driver, sends the accessibility tree and a
screenshot to the model, and asks for exactly ONE next action. The model
never sees more than the current screen and never plans ahead -- it has no
memory of earlier turns, only whatever the current screenshot shows.

The model does not decide when the goal is met. After every action the loop
re-perceives and checks the goal's success checkpoint itself (the same
checkpoint syntax and _check() function replay.py uses) -- the system, not
the model, is the source of truth for "are we done." A successful run is
compiled into a draft CapabilityArtifact, in the same shape replay.py
expects to run.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Literal, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel

from .driver import Observation, PlaywrightBankDriver
from .env import load_dotenv
from .models import (ApprovalState, CapabilityArtifact, Locator, Output,
                     Param, Step)
from .replay import _check  # same checkpoint semantics discovery and replay share

DEFAULT_MODEL = "gemini-2.5-flash"
MAX_STEPS_DEFAULT = 8
TIMEOUT_S_DEFAULT = 120

StopReason = Literal["success", "max_steps", "timeout", "dead_end"]

PROMPT = """You control a web browser via a driver, ONE action at a time. \
You never plan more than a single step ahead -- decide only what to do next, \
given what is on screen right now.

GOAL: {goal}

You are shown the CURRENT page as an accessibility tree and a screenshot.
Pick the SINGLE next action that makes progress toward the goal.

Action vocabulary:
  type  -- type text into a text input. value must be the EXACT literal
           text to type, nothing else appended.
  click -- click a button or link.
  read  -- the piece of information the goal needs is already visible on
           screen; name it in target_description (e.g. "the savings
           balance"). Use this instead of clicking anything.
  wait  -- the page looks like it is still loading.
  done  -- you believe the goal is already fully satisfied by what's on
           screen; do not choose this unless the requested information is
           visibly present.

target_description: plain English, as if pointing at the control on screen
  (e.g. "the Search button", "the member ID text box").
value: null unless action is "type".

ACCESSIBILITY TREE:
{tree}
"""


class NextAction(BaseModel):
    action: Literal["type", "click", "read", "wait", "done"]
    target_description: str
    value: Optional[str] = None


@dataclass
class TurnLog:
    turn: int
    observed_screen: str
    decided_action: str
    decided_target: str
    decided_value: Optional[str]
    ladder: list[Locator] = field(default_factory=list)
    resolved_rung: Optional[int] = None
    resolved_strategy: Optional[str] = None
    resolved_field_key: Optional[str] = None  # for "read": which field this was
    screen_after: str = ""
    ok: bool = True
    note: str = ""

    def to_log_dict(self) -> dict:
        """Brief, redaction-safe view for the saved step log: what was
        observed, what the model decided, what actually ran. No raw HTML,
        no full accessibility tree, no screenshot bytes -- those would bloat
        the log and the values on this page (member id/name/balance) are
        fake demo data anyway, not the kind of thing that needs hiding.
        """
        return {
            "turn": self.turn,
            "observed_screen": self.observed_screen,
            "model_decided": {"action": self.decided_action,
                              "target_description": self.decided_target,
                              "value": self.decided_value},
            "ladder_tried": [f"{l.strategy}={l.value}" for l in self.ladder],
            "resolved_rung": self.resolved_rung,
            "resolved_strategy": self.resolved_strategy,
            "screen_after": self.screen_after,
            "ok": self.ok,
            "note": self.note,
        }


@dataclass
class DiscoveryResult:
    stop_reason: StopReason
    turns: list[TurnLog]
    artifact: Optional[CapabilityArtifact]
    model_calls: int
    goal: str
    model_name: str
    final_screenshot: Optional[bytes] = None


def _get_client() -> genai.Client:
    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Checked process environment and a "
            "local .env file. Set it before running discovery -- no fake "
            "or simulated run will be substituted.")
    return genai.Client(api_key=api_key)  # SDK never logs the key itself


def _decide(client: genai.Client, model_name: str, goal: str,
            obs: Observation) -> NextAction:
    prompt = PROMPT.format(goal=goal, tree=obs.accessibility_tree or "(empty)")
    contents: list = [prompt]
    if obs.screenshot:
        contents.append(types.Part.from_bytes(data=obs.screenshot,
                                              mime_type="image/png"))
    resp = client.models.generate_content(
        model=model_name,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=NextAction,
        ),
    )
    return resp.parsed


def _best_label_match(description: str, fields: dict[str, str]) -> Optional[str]:
    """Match the model's free-text description of a field against the
    labels actually present on the current screen (recovered from
    perceive()'s already-slugified field keys, e.g. "savings_balance" ->
    "Savings Balance"), by word overlap. Returns the slug key, not the label.
    """
    desc_words = set(description.lower().split())
    best_key, best_score = None, 0
    for key in fields:
        label_words = set(key.replace("_", " ").split())
        score = len(desc_words & label_words)
        if score > best_score:
            best_key, best_score = key, score
    return best_key


def _build_ladder(action: str, description: str,
                  obs: Observation) -> tuple[list[Locator], Optional[str]]:
    """Same strategy vocabulary and top-down order as PlaywrightBankDriver's
    ladder (role_name -> attribute -> label_anchor -> coordinates). Unlike a
    hand-written recipe, discovery can only guess at role+name or a nearby
    label -- it has no way to know an HTML attribute like name="member_id"
    exists, since that's not in the accessibility tree or the screenshot.
    """
    d = description.strip()
    if action == "type":
        return [
            Locator(strategy="role_name", value=f"textbox::{d}"),
            Locator(strategy="role_name", value="textbox::"),  # any textbox
        ], None
    if action == "click":
        return [
            Locator(strategy="role_name", value=f"button::{d}"),
            Locator(strategy="role_name", value=f"link::{d}"),
            Locator(strategy="role_name", value="button::"),  # any button
        ], None
    if action == "read":
        # Rungs 1-2 use the model's own raw phrasing as a guess -- it has no
        # way to know the page's exact label text yet. Using the RESOLVED
        # label (below) for a role_name guess would be a bug, not a guess:
        # a <td> whose own accessible name equals the label text (e.g.
        # "Savings Balance") IS the label cell, so that rung would silently
        # match the wrong cell instead of legitimately failing.
        field_key = _best_label_match(d, obs.fields)
        anchor = field_key.replace("_", " ").title() if field_key else d
        return [
            Locator(strategy="role_name", value=f"cell::{d}"),
            Locator(strategy="attribute", value=f'[aria-label="{d}"]'),
            Locator(strategy="label_anchor", value=anchor),
        ], field_key
    return [], None


def run_discovery(driver: PlaywrightBankDriver, goal: str, entry_point: str,
                  inputs: dict[str, str], success_checkpoint: str,
                  model_name: str = DEFAULT_MODEL,
                  max_steps: int = MAX_STEPS_DEFAULT,
                  timeout_s: int = TIMEOUT_S_DEFAULT) -> DiscoveryResult:
    client = _get_client()

    # Bootstrap navigation is system-level, not a model decision -- the
    # model is never asked to invent a URL. It takes over once there's a
    # real screen to look at.
    driver.act("navigate", value=entry_point)
    obs = driver.perceive()

    turns: list[TurnLog] = []
    model_calls = 0
    start = time.monotonic()
    stop_reason: StopReason = "max_steps"

    for turn_no in range(1, max_steps + 1):
        if time.monotonic() - start > timeout_s:
            stop_reason = "timeout"
            break

        decision = _decide(client, model_name, goal, obs)
        model_calls += 1

        if decision.action == "done":
            done_ok = _check(obs, success_checkpoint)
            turns.append(TurnLog(
                turn=turn_no, observed_screen=obs.screen,
                decided_action="done", decided_target=decision.target_description,
                decided_value=None, screen_after=obs.screen, ok=done_ok,
                note="checkpoint holds" if done_ok
                     else "model declared done but checkpoint does not hold"))
            stop_reason = "success" if done_ok else "dead_end"
            break

        ladder, field_key = _build_ladder(decision.action,
                                          decision.target_description, obs)
        pre_screen = obs.screen
        prior_log_len = len(driver.rung_log)
        result = driver.act(decision.action, target=ladder, value=decision.value)
        # "wait" never consults the ladder, so guard against picking up a
        # stale rung_log entry from an earlier turn.
        rung = driver.rung_log[-1] if len(driver.rung_log) > prior_log_len else {}
        obs = driver.perceive()

        turns.append(TurnLog(
            turn=turn_no, observed_screen=pre_screen,
            decided_action=decision.action,
            decided_target=decision.target_description,
            decided_value=decision.value, ladder=ladder,
            resolved_rung=rung.get("rung"), resolved_strategy=rung.get("strategy"),
            resolved_field_key=field_key, screen_after=obs.screen,
            ok=result.ok, note=result.note))

        if not result.ok:
            stop_reason = "dead_end"
            break
        # Only "read" can conclude the goal. perceive() extracts every
        # visible field generically, so the checkpoint can go true as a
        # side effect of a "click" alone (e.g. the balance is already on
        # the page that navigation lands on) -- checking after every action
        # would let the loop stop before the model ever explicitly reads
        # the value, producing a draft with a passing success checkpoint
        # but no declared output. Require an explicit read, same as the
        # hand-written recipe's dedicated s3 step.
        if decision.action == "read" and _check(obs, success_checkpoint):
            stop_reason = "success"
            break
    else:
        stop_reason = "max_steps"

    artifact = None
    if stop_reason == "success":
        artifact = _compile_artifact(goal, entry_point, inputs, turns,
                                     success_checkpoint)

    return DiscoveryResult(stop_reason=stop_reason, turns=turns, artifact=artifact,
                           model_calls=model_calls, goal=goal, model_name=model_name,
                           final_screenshot=obs.screenshot)


def _compile_artifact(goal: str, entry_point: str, inputs: dict[str, str],
                      turns: list[TurnLog], success_checkpoint: str) -> CapabilityArtifact:
    steps: list[Step] = []
    outputs: list[Output] = []

    for i, t in enumerate(turns, start=1):
        step_id = f"d{i}"
        value_from = None
        if t.decided_action == "type":
            # A recipe step can't carry a literal (see Step in models.py --
            # only value_from) by design: recipes are meant to be replayed
            # with different data, not frozen to one run's literal input.
            for name, val in inputs.items():
                if t.decided_value and t.decided_value.strip() == val:
                    value_from = f"input.{name}"
                    break

        checkpoint = None
        if t.decided_action == "click":
            checkpoint = f"screen=={t.screen_after}"
        elif t.decided_action == "read" and t.resolved_field_key:
            checkpoint = f"field:{t.resolved_field_key}"
            outputs.append(Output(name=t.resolved_field_key, type="string",
                                  source_step=step_id))

        steps.append(Step(id=step_id, action=t.decided_action, target=t.ladder,
                          value_from=value_from, checkpoint=checkpoint))

    return CapabilityArtifact(
        id="cap_lookup_savings_discovered",
        name="lookup_savings_discovered",
        description=goal,
        target={"app_id": "legacy-core-banking", "entry_point": entry_point,
                "allowlist_ref": "core-banking"},
        inputs=[Param(name=name, type="string") for name in inputs],
        outputs=outputs,
        steps=steps,
        success=success_checkpoint,
        known_outcomes=[],  # only the success path was exercised this run
        redaction=[],
        approval_state=ApprovalState.draft,
        provenance={"recorded_by": "real_discovery", "goal": goal},
    )
