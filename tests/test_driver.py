"""The locator ladder, against the REAL PlaywrightBankDriver -- not a fake
stand-in, since FakeBankDriver's _resolve() is a trivial one-liner with no
fallback behavior to test.

Self-contained: loads static HTML directly into the page (no navigation),
so this needs a Chromium browser but no external server running. Slower
than the pure-Python tests in this suite (a browser launch), which is why
it's kept to this one case rather than duplicated across scenarios.
"""
from app.driver import PlaywrightBankDriver
from app.models import Locator


def test_locator_ladder_falls_through_to_lower_rung():
    """Rung 1 (role+name) is a plausible guess that genuinely does not
    match anything on this page -- there is no element named "Nonexistent
    Name". Rung 2 (label_anchor) finds the "Label" cell and reads its
    sibling. Proves the ladder actually walks top-down and stops at the
    first rung that resolves, not just that the last rung happens to work.
    """
    with PlaywrightBankDriver(headless=True) as driver:
        driver.page.set_content(
            "<table><tr><td>Label</td><td>Target Value</td></tr></table>")

        ladder = [
            Locator(strategy="role_name", value="cell::Nonexistent Name"),
            Locator(strategy="label_anchor", value="Label"),
        ]
        result = driver.act("read", target=ladder)

        assert result.ok
        assert result.note == "Target Value"
        assert driver.rung_log[-1]["rung"] == 2
        assert driver.rung_log[-1]["strategy"] == "label_anchor"
