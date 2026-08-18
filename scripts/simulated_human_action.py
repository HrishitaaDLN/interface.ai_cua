"""Stands in for a human's own tool attaching to the SAME live browser
session the automation just paused on, over Chrome DevTools Protocol. This
is a genuinely separate OS process, not a function call inside the
automation's own script, same as any real external tool a human would use
to look at the paused session.

Usage:
    python scripts/simulated_human_action.py <cdp_port> <fix_url>
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright


def main() -> None:
    port, fix_url = sys.argv[1], sys.argv[2]
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f"http://localhost:{port}")
        page = browser.contexts[0].pages[0]
        print(f"human attached to the paused session, was on: {page.url}")
        page.goto(fix_url, wait_until="load")
        print(f"human action complete, now on: {page.url}")
        # Deliberately not closing the browser here: it belongs to the
        # automation's session, not to this process.


if __name__ == "__main__":
    main()
