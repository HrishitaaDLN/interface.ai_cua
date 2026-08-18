"""The load-bearing seam: how we perceive and act on a surface.

Interface is two verbs. A real WebDriver drives Playwright (below). The
FakeBankDriver simulates a tiny legacy bank app in memory so the whole system
runs end to end with no browser and no model.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Protocol
from urllib.parse import urlparse

from .models import Locator

logger = logging.getLogger("app.driver")


@dataclass
class Observation:
    screen: str                       # which "page" we're on
    controls: dict[str, str]          # control name -> current value/label
    fields: dict[str, str] = field(default_factory=dict)  # readable data
    # Optional: only a real driver fills these in. FakeBankDriver leaves them
    # None, and replay.py never reads them, so this is additive, not a
    # breaking change to the interface.
    accessibility_tree: Optional[str] = None
    screenshot: Optional[bytes] = None


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

    def __init__(self, hide_balance: bool = False, slow_turns: int = 0):
        self.screen = "search"
        self.query = ""
        self.current: Optional[str] = None
        self.hide_balance = hide_balance
        self.slow_turns = slow_turns          # perceive()s the detail screen
        self._slow_remaining = 0              # stays "loading" for, before settling
        self.calls: list[str] = []            # action call log, for resume tests

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
        if self._slow_remaining > 0:
            return Observation(screen="loading", controls={})
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
        self.calls.append(f"{action}:{name}")
        if action == "type" and name == "search_box":
            self.query = value or ""
            return ActionResult(True)
        if action == "click" and name == "search_button":
            if self.query in MEMBERS:
                self.current, self.screen = self.query, "detail"
                self._slow_remaining = self.slow_turns
            else:
                self.screen = "not_found"
            return ActionResult(True)
        if action == "wait":
            if self._slow_remaining > 0:
                self._slow_remaining -= 1
            return ActionResult(True)
        if action == "read":
            return ActionResult(True)
        return ActionResult(False, f"unknown action {action} on {name}")


# ---- real Playwright driver -------------------------------------------------
# Same perceive()/act() shapes as FakeBankDriver above. Nothing that calls a
# Driver needs to know which one it's holding.
#
# Locator.value encoding per strategy (a recipe stores these, this class
# reads them):
#   role_name    "<role>::<accessible name>"   e.g. "button::Search"
#   attribute    a CSS selector                e.g. 'input[name="member_id"]'
#   label_anchor the exact text of a nearby <td> label, e.g. "Savings Balance"
#   coordinates  "<x>,<y>" page coordinates, e.g. "245,59"

# One short, bounded chance per attempt for an in-flight navigation to
# settle. Never the whole wait by itself -- replay.py's recoverable-retry
# loop is what decides whether to ask for another one or give up.
NAV_ATTEMPT_TIMEOUT_MS = 1200


class _CoordinateTarget:
    """Makes a raw (x, y) point look like a Playwright Locator, so act()
    doesn't need a special case for the last-resort rung."""

    def __init__(self, page, value: str):
        x, y = value.split(",")
        self.page, self.x, self.y = page, float(x), float(y)

    def click(self) -> None:
        self.page.mouse.click(self.x, self.y)

    def fill(self, value: str) -> None:
        self.page.mouse.click(self.x, self.y)
        self.page.keyboard.type(value)

    def inner_text(self) -> str:
        return self.page.evaluate(
            "([x, y]) => document.elementFromPoint(x, y)?.innerText ?? ''",
            [self.x, self.y],
        )

    def wait_for(self, **kwargs) -> None:
        pass  # a screen point always "exists"; nothing to wait for


class PlaywrightBankDriver:
    """Drives the real legacy bank page at http://localhost:8080 (or
    wherever base_url points) with Playwright.
    """

    def __init__(self, base_url: str = "http://localhost:8080",
                 headless: bool = True, remote_debug_port: Optional[int] = None):
        from playwright.sync_api import sync_playwright  # local: no hard
        # dependency on Playwright for anything that only uses FakeBankDriver

        self.base_url = base_url.rstrip("/")
        self._pw = sync_playwright().start()
        # remote_debug_port is what makes a same-session human handoff
        # possible: with it set, a completely separate Playwright (or any
        # CDP-speaking tool) can attach to THIS running browser via
        # chromium.connect_over_cdp(f"http://localhost:{port}") and act on
        # the exact page this driver holds, not a fresh one. Off by default
        # since ordinary replay runs have no need to expose a debug port.
        args = [f"--remote-debugging-port={remote_debug_port}"] if remote_debug_port else []
        self._browser = self._pw.chromium.launch(headless=headless, args=args)
        self.page = self._browser.new_page()
        self.rung_log: list[dict] = []  # which ladder rung matched, in order
        self.remote_debug_port = remote_debug_port

    def cdp_session_id(self) -> Optional[str]:
        """The browser's own CDP target id for this page, not something
        this project invents. Fetched from Chrome's debug HTTP endpoint
        (http://localhost:<port>/json). Two evidence files that quote the
        SAME id are provably talking about the same live tab, not just
        claiming to. Returns None if remote_debug_port wasn't set.
        """
        if not self.remote_debug_port:
            return None
        import httpx
        targets = httpx.get(f"http://localhost:{self.remote_debug_port}/json").json()
        for t in targets:
            if t.get("type") == "page" and t.get("url") == self.page.url:
                return t.get("id")
        return targets[0].get("id") if targets else None

    def close(self) -> None:
        self._browser.close()
        self._pw.stop()

    def __enter__(self) -> "PlaywrightBankDriver":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- perceive -------------------------------------------------------

    def perceive(self) -> Observation:
        # page.url is Playwright's own cached copy of the last navigation
        # IT observed. It goes stale if some other CDP client (a human's
        # own tool, attached via remote_debug_port) navigates the page
        # instead -- confirmed directly: the live DOM updates correctly but
        # page.url keeps showing the pre-handoff address. Reading the
        # location straight from the page's own JS context is what perceive
        # depends on being right after a human's own action, not this
        # driver's. But evaluate() can itself race a navigation already in
        # flight (confirmed directly too: the recoverable slow-load scenario
        # is exactly a mid-navigation perceive(), and it raised "Execution
        # context was destroyed"). Fall back to the cached property then --
        # it may be a step stale, but the content checks below will already
        # reflect the in-between state and correctly fail the checkpoint,
        # which is what triggers the bounded retry that scenario relies on.
        try:
            live_url = self.page.evaluate("() => window.location.href")
        except Exception:
            live_url = self.page.url
        path = urlparse(live_url).path
        body = self.page.locator("body")
        body_text = body.inner_text()
        tree = body.aria_snapshot()
        screenshot = self.page.screenshot()

        if "No such member" in body_text:
            screen = "not_found"
        elif path.startswith("/core-banking/member"):
            screen = "detail"
        else:
            screen = "search"

        fields: dict[str, str] = {}
        controls: dict[str, str] = {}

        if screen == "detail":
            # Every two-cell table row is "label -> value". Slugifying the
            # label ("Savings Balance" -> "savings_balance") is what lines
            # these up with the field names a recipe's outputs/checkpoints
            # expect, with no per-field parsing code.
            for row in self.page.locator("table tr").all():
                cells = row.locator("td").all()
                if len(cells) == 2:
                    label = cells[0].inner_text().strip()
                    value = cells[1].inner_text().strip()
                    fields[label.lower().replace(" ", "_")] = value
        elif screen == "not_found":
            controls["message"] = body_text.strip()
        else:  # search
            box = self.page.locator('input[name="member_id"]')
            controls["search_box"] = box.input_value() if box.count() else ""
            controls["search_button"] = "Search"

        return Observation(screen=screen, controls=controls, fields=fields,
                           accessibility_tree=tree, screenshot=screenshot)

    # -- act --------------------------------------------------------------

    def act(self, action: str, target: Optional[list[Locator]] = None,
            value: Optional[str] = None) -> ActionResult:
        if action == "navigate":
            url = value or (target[0].value if target else self.base_url)
            if url.startswith("/"):
                url = self.base_url + url
            self.page.goto(url, wait_until="load")
            return ActionResult(True)

        if action == "wait":
            self._wait_briefly_for_navigation()
            return ActionResult(True)

        if not target:
            return ActionResult(False, f"no locator ladder given for '{action}'")

        found, rung, strategy = self._resolve(target)
        label = self._ladder_label(target)
        self.rung_log.append({"control": label, "rung": rung,
                              "strategy": strategy, "ok": found is not None})
        if found is None:
            logger.warning("locator ladder exhausted for %r", label)
            return ActionResult(False, f"no rung resolved for {label}")
        logger.info("%r resolved via rung %d (%s)", label, rung, strategy)

        if action == "type":
            found.fill(value or "")
            return ActionResult(True)
        if action == "click":
            # no_wait_after: the click itself is mechanical and always
            # succeeds. Whether the page it triggers has finished settling
            # is a separate, bounded question -- replay.py's recoverable
            # retry loop is what decides how many more short chances to
            # give it before treating it as a hard failure.
            found.click(no_wait_after=True)
            self._wait_briefly_for_navigation()
            return ActionResult(True)
        if action == "read":
            found.wait_for(state="visible")
            return ActionResult(True, note=found.inner_text())
        return ActionResult(False, f"unsupported action '{action}'")

    def _wait_briefly_for_navigation(self) -> None:
        """One short, bounded wait for a navigation to complete. Times out
        quietly rather than raising -- a bounded miss here is normal and
        expected, not an error; the caller decides what to do about it.
        """
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        try:
            with self.page.expect_navigation(timeout=NAV_ATTEMPT_TIMEOUT_MS):
                pass
        except PlaywrightTimeoutError:
            pass

    # -- locator ladder ---------------------------------------------------

    def _resolve(self, target: list[Locator]):
        """Try each locator top-down. Returns (playwright-locator-like,
        rung number, strategy) for the first one that actually exists on the
        page, or (None, None, None) if the whole ladder is exhausted.
        """
        for rung, loc in enumerate(target, start=1):
            found = self._try_strategy(loc)
            if found is not None:
                return found, rung, loc.strategy
        return None, None, None

    def _try_strategy(self, loc: Locator):
        try:
            if loc.strategy == "role_name":
                role, _, name = loc.value.partition("::")
                pl = (self.page.get_by_role(role, name=name, exact=True)
                      if name else self.page.get_by_role(role))
            elif loc.strategy == "attribute":
                pl = self.page.locator(loc.value)
            elif loc.strategy == "label_anchor":
                # Nearby-label anchor: find the <td> with this exact text,
                # then read its sibling cell in the same row. There is no
                # accessible-name or attribute link for this on a legacy
                # table page — position relative to a label is all we get.
                safe = loc.value.replace('"', '\\"')
                pl = self.page.locator(
                    f'xpath=//td[normalize-space(text())="{safe}"]'
                    f"/following-sibling::td[1]")
            elif loc.strategy == "coordinates":
                return _CoordinateTarget(self.page, loc.value)
            else:
                return None
            return pl if pl.count() > 0 else None
        except Exception:
            return None

    @staticmethod
    def _ladder_label(target: list[Locator]) -> str:
        """Best-effort human label for logging, drawn from whichever rung
        carries a readable name. Doesn't affect resolution, only the log.
        """
        for loc in target:
            if loc.strategy == "role_name" and "::" in loc.value:
                return loc.value.split("::", 1)[1]
            if loc.strategy == "label_anchor":
                return loc.value
        return target[0].value if target else "?"
