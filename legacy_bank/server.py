"""A tiny, deliberately hostile server-rendered "legacy" bank page.

This is the SURFACE being automated — a stand-in for a real internal banking
screen. It is a separate process from the automation service in app/: one
system is the thing being driven, the other is the thing driving it. They
should never share code, only a URL.

Deliberately legacy, on purpose:
  - table-based layout, no CSS, no JS
  - no id attributes, no data-* attributes, no data-testid — nothing added
    "for testing"
  - no <label for="">, no semantic tags (<header>/<main>/<section>) — the
    "Member ID" caption sitting next to the input is just table-cell text,
    not something the accessibility tree formally associates with the field
  - a submit <input> instead of a styled <button>

That hostility is the point. Step 3's locator ladder (role+name, then
attribute, then nearby-label anchor, then coordinates) exists BECAUSE real
legacy screens look like this, not because it's a fun exercise.

Run standalone, separate from the automation service:
    uvicorn legacy_bank.server:app --port 8080 --reload
"""
from __future__ import annotations

import asyncio
import html

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="Core Banking (legacy, fake)")

# Fake data only. 99999 is deliberately left unseeded so it demonstrates the
# not-found screen without any special-case code.
MEMBERS = {
    "12345": {"name": "Ada Lovelace", "savings": "$4,210.55"},
    "67890": {"name": "Alan Turing", "savings": "$980.00"},
}

SLOW_LOAD_SECONDS = 3


def _page(body: str) -> str:
    # No CSS, no JS, bgcolor attribute instead of a stylesheet — ugly on
    # purpose.
    return f"<html><head><title>Core Banking</title></head>" \
           f"<body bgcolor=\"#ffffff\">{body}</body></html>"


@app.get("/core-banking", response_class=HTMLResponse)
def search_page(slow: int = 0, hide_balance: int = 0):
    """First screen: a member search box."""
    # slow/hide_balance ride along as hidden fields so a condition "primed"
    # on the search URL still applies once you actually search.
    body = f"""
    <table border="1" cellpadding="6" cellspacing="0">
      <tr><td colspan="2"><b>Member Search</b></td></tr>
      <tr>
        <td>Member ID</td>
        <td>
          <form method="get" action="/core-banking/member">
            <input type="text" name="member_id" size="10">
            <input type="hidden" name="slow" value="{slow}">
            <input type="hidden" name="hide_balance" value="{hide_balance}">
            <input type="submit" value="Search">
          </form>
        </td>
      </tr>
    </table>
    """
    return _page(body)


@app.get("/core-banking/member", response_class=HTMLResponse)
async def member_detail(member_id: str = "", slow: int = 0, hide_balance: int = 0):
    """Second screen: member detail (name + savings balance)."""
    if slow:
        await asyncio.sleep(SLOW_LOAD_SECONDS)

    member = MEMBERS.get(member_id)
    if not member:
        body = f"""
        <table border="1" cellpadding="6" cellspacing="0">
          <tr><td><b>No such member: {html.escape(member_id)}</b></td></tr>
        </table>
        """
        return _page(body)

    rows = (
        f"<tr><td>Member ID</td><td>{html.escape(member_id)}</td></tr>"
        f"<tr><td>Name</td><td>{html.escape(member['name'])}</td></tr>"
    )
    if not hide_balance:
        rows += (
            f"<tr><td>Savings Balance</td>"
            f"<td>{html.escape(member['savings'])}</td></tr>"
        )
    # hidden-balance mode omits the row entirely rather than leaving it
    # blank — the field must be genuinely absent, not empty.

    body = f"""
    <table border="1" cellpadding="6" cellspacing="0">
      <tr><td colspan="2"><b>Member {html.escape(member_id)}</b></td></tr>
      {rows}
    </table>
    """
    return _page(body)
