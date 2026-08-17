"""Drives the REAL legacy bank page once with PlaywrightBankDriver: search
12345, land on the detail screen, read the balance. Prints which locator-
ladder rung resolved each control, since that's the drift signal the design
relies on.

This talks to the driver directly. It does not go through the recipe/replay
engine (app/store.py, app/replay.py) — those aren't touched by this step.

Precondition: the legacy bank server must already be running:
    uvicorn legacy_bank.server:app --port 8080

Run:
    python scripts/demo_playwright_driver.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.driver import PlaywrightBankDriver
from app.models import Locator

BASE_URL = "http://localhost:8080"

# Each ladder is ordered best-first, same shape a recorded recipe would use.
# Rungs 1-2 for the balance field are plausible guesses a discovery run
# might record and are EXPECTED to fail on this page — there is no
# accessible name or attribute for an unlabeled table cell, only its
# position next to a label.
SEARCH_BOX = [
    Locator(strategy="role_name", value="textbox::Member ID"),
    Locator(strategy="attribute", value='input[name="member_id"]'),
]
SEARCH_BUTTON = [
    Locator(strategy="role_name", value="button::Search"),
    Locator(strategy="attribute", value='input[type="submit"]'),
]
SAVINGS_BALANCE = [
    Locator(strategy="role_name", value="cell::Savings Balance Amount"),
    Locator(strategy="attribute", value='td[data-field="savings_balance"]'),
    Locator(strategy="label_anchor", value="Savings Balance"),
]


def main() -> None:
    with PlaywrightBankDriver(base_url=BASE_URL, headless=True) as driver:
        try:
            driver.act("navigate", value="/core-banking")
        except Exception as exc:
            print(f"Could not reach {BASE_URL} — is the legacy bank server "
                  f"running? (uvicorn legacy_bank.server:app --port 8080)")
            raise SystemExit(1) from exc

        obs = driver.perceive()
        print(f"1. loaded search page -> screen={obs.screen!r}")

        driver.act("type", target=SEARCH_BOX, value="12345")
        print("2. typed '12345' into search box")

        driver.act("click", target=SEARCH_BUTTON)
        obs = driver.perceive()
        print(f"3. clicked search -> screen={obs.screen!r}")
        assert obs.screen == "detail", f"expected detail screen, got {obs.screen}"

        result = driver.act("read", target=SAVINGS_BALANCE)
        obs = driver.perceive()
        print(f"4. read balance -> fields={obs.fields}")
        assert obs.fields.get("savings_balance") == "$4,210.55"

        print("\n--- locator ladder log (which rung resolved each control) ---")
        for entry in driver.rung_log:
            status = f"rung {entry['rung']} ({entry['strategy']})" if entry["ok"] \
                else "NOT RESOLVED"
            print(f"  {entry['control']!r}: {status}")

        print(f"\naccessibility tree length: {len(obs.accessibility_tree or '')} chars")
        print(f"screenshot: {len(obs.screenshot or b'')} bytes (in-memory PNG)")
        print("\nOK: search -> detail -> balance read, end to end.")


if __name__ == "__main__":
    main()
