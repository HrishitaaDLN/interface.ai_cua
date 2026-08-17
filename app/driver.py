"""The load-bearing seam: how we perceive and act on a surface.

Interface is two verbs. A real WebDriver would drive Playwright here. The
FakeDriver simulates a tiny legacy bank app in memory so the whole system
runs end to end with no browser and no model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from .models import Locator


@dataclass
class Observation:
    screen: str                       # which "page" we're on
    controls: dict[str, str]          # control name -> current value/label
    fields: dict[str, str] = field(default_factory=dict)  # readable data


@dataclass
class ActionResult:
    ok: bool
    note: str = ""


class Driver(Protocol):
    def perceive(self) -> Observation: ...
    def act(self, action: str, target: Optional[list[Locator]] = None,
            value: Optional[str] = None) -> ActionResult: ...


# ---- fake bank app ---------------------------------------------------------

MEMBERS = {
    "12345": {"name": "Ada Lovelace", "savings": "$4,210.55"},
    "67890": {"name": "Alan Turing", "savings": "$980.00"},
}


class FakeBankDriver:
    """A search -> detail flow. Inject failures via the flags below."""

    def __init__(self, hide_balance: bool = False):
        self.screen = "search"
        self.query = ""
        self.current: Optional[str] = None
        self.hide_balance = hide_balance

    def _resolve(self, target: Optional[list[Locator]]) -> str:
        # A real driver walks the ladder against the live surface. Here we
        # just take the top locator's value as the control name.
        return target[0].value if target else ""

    def perceive(self) -> Observation:
        if self.screen == "search":
            return Observation(screen="search",
                               controls={"search_box": self.query,
                                         "search_button": "Search"})
        if self.screen == "not_found":
            return Observation(screen="not_found",
                               controls={"message": "No such member"})
        # detail screen
        m = MEMBERS[self.current]  # type: ignore[index]
        fields = {"member_id": self.current, "name": m["name"]}
        if not self.hide_balance:
            fields["savings_balance"] = m["savings"]
        return Observation(screen="detail",
                           controls={"heading": f"Member {self.current}"},
                           fields=fields)

    def act(self, action, target=None, value=None) -> ActionResult:
        name = self._resolve(target)
        if action == "type" and name == "search_box":
            self.query = value or ""
            return ActionResult(True)
        if action == "click" and name == "search_button":
            if self.query in MEMBERS:
                self.current, self.screen = self.query, "detail"
            else:
                self.screen = "not_found"
            return ActionResult(True)
        if action in ("read", "wait"):
            return ActionResult(True)
        return ActionResult(False, f"unknown action {action} on {name}")
