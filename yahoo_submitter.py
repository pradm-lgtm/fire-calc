#!/usr/bin/env python3
"""
Placing Yahoo waiver claims in a browser, because the API cannot.

Yahoo's Fantasy API grants read access only - that is not a limit of our
approval, it is what Yahoo offers anyone - so a claim has to be made the
way a person makes it. The reading is done properly through the API and
only the clicking happens here, which is the right division: nothing is
scraped that an endpoint would have answered.

    python3 yahoo_submitter.py --probe 470.l.715420

WHY THIS STARTS AS A PROBE AND NOT A SUBMITTER

The Sleeper submitter was written twice, because the first version encoded
what I assumed the page did. So this one reports what is actually there
before anything is automated against it: the probe opens the real waiver
page in your signed-in Chrome and describes the controls it finds, and the
selectors are written afterwards, from that.

WHAT IS DIFFERENT FROM SLEEPER

These leagues run waiver priority, not FAAB. There is no bid to type and
no budget to spend down; the cost of a claim is your place in the queue,
which you get back at the bottom. So the claim form is expected to be
simpler than Sleeper's, and the interesting question is the drop: whether
Yahoo demands one up front the way Sleeper does.

The browser plumbing - attaching to a Chrome you signed into yourself,
rather than driving a login - is submitter.py's, reused rather than
reimplemented.
"""

import argparse
import re
import sys

import submitter

SITE = "https://football.fantasysports.yahoo.com"


def league_number(league_key):
    """'470.l.715420' -> '715420', the id the website uses.

    The API speaks in league keys, which carry the game ("470" is this
    season's NFL) and the league. Every URL on the website wants the middle
    number on its own, so one of them has to be converted and it had better
    not be by hand.
    """
    text = str(league_key or "").strip()
    if not text:
        return None
    match = re.match(r"^\d+\.l\.(\d+)$", text)
    if match:
        return match.group(1)
    return text if text.isdigit() else None


def players_url(league_key):
    """The free agents page for a league."""
    number = league_number(league_key)
    return f"{SITE}/f1/{number}/players?status=A" if number else None


def team_url(team_key):
    """'470.l.715420.t.2' -> that team's page."""
    match = re.match(r"^\d+\.l\.(\d+)\.t\.(\d+)$", str(team_key or "").strip())
    return f"{SITE}/f1/{match.group(1)}/{match.group(2)}" if match else None


def signed_out(url, title):
    """Yahoo bounces a signed-out visitor to login, sometimes via a redirect.

    Judged from the address rather than from the presence of a sign-in
    button, because a signed-in page carries one of those too.
    """
    haystack = f"{url} {title}".lower()
    return any(mark in haystack for mark in
               ("login.yahoo", "/login", "sign in to yahoo"))


def describe(found):
    """Turn what the probe saw into something worth reading.

    Kept apart from the page so the reporting can be tested without a
    browser, and so the interesting judgement - what these counts mean for
    the submitter - lives somewhere it can be argued with.
    """
    lines = []
    add = found.get("add_controls") or 0
    lines.append(f"  add controls        {add}")
    if not add:
        lines.append("      none found, so either the page did not load as")
        lines.append("      expected or the markup has moved")
    lines.append(f"  player rows         {found.get('rows') or 0}")
    lines.append(f"  a dialog is open    {'yes' if found.get('dialog') else 'no'}")
    drop = found.get("drop_controls")
    if drop is None:
        lines.append("  drop required       not reached")
    else:
        lines.append(f"  drop controls       {drop}")
        lines.append("      Yahoo asks for the drop in the same step"
                     if drop else
                     "      no drop control here, so a claim may not need one")
    return "\n".join(lines)


def look(page):
    """Count the controls a claim would need, without clicking anything."""
    def count(selector):
        try:
            return page.locator(selector).count()
        except Exception:
            return 0
    return {
        "rows": count("table tbody tr"),
        "add_controls": count("a[href*='addplayer'], button:has-text('Add')"),
        "dialog": bool(count("[role='dialog'], .Modal, #modal")),
        "drop_controls": None,
    }


# Words that commit a claim. The probe must never click one: opening the
# form is reversible and placing a claim is not, and a tool whose job is to
# find out what the page does has no business changing anything while it
# does so. Kept as data so a test can check nothing here clicks them.
COMMITTING = ("submit", "place claim", "confirm", "add player")


def add_control(page, name=None):
    """The Add link for one player, or the first one on the page.

    Scoped to the row carrying the name, because the page holds fifty-odd
    of these and the one next to the player you asked about is the only one
    worth clicking.
    """
    links = "a[href*='addplayer'], button:has-text('Add')"
    if not name:
        return page.locator(links).first
    row = page.locator("tr", has_text=name).first
    found = row.locator(links).first
    return found if found.count() else None


def describe_claim(found):
    """What the form after Add turns out to want."""
    lines = [f"  opened as           {found.get('shape') or 'unknown'}"]
    if found.get("url"):
        lines.append(f"  address             {found['url'][:90]}")
    drop = found.get("drop_controls") or 0
    lines.append(f"  drop controls       {drop}")
    lines.append("      Yahoo wants the drop chosen here, like Sleeper"
                 if drop else
                 "      no drop control, so a claim can be filed on its own")
    bids = found.get("bid_inputs") or 0
    lines.append(f"  bid or FAAB fields  {bids}")
    if bids:
        lines.append("      unexpected in a waiver-priority league - worth"
                     " a look")
    buttons = found.get("buttons") or []
    lines.append(f"  buttons             {', '.join(buttons) or 'none found'}")
    return "\n".join(lines)


def look_at_claim(page):
    """Read the claim form. Clicks nothing."""
    def count(selector):
        try:
            return page.locator(selector).count()
        except Exception:
            return 0

    labels = []
    try:
        for i in range(min(count("button, input[type=submit]"), 12)):
            text = (page.locator("button, input[type=submit]").nth(i)
                    .inner_text(timeout=1000) or "").strip()
            if text and text not in labels:
                labels.append(text[:30])
    except Exception:
        pass
    dialog = count("[role='dialog'], .Modal, #modal")
    return {
        "shape": "a dialog on the same page" if dialog else "a new page",
        "url": page.url,
        "drop_controls": count(
            "select[name*='drop'], input[name*='drop'], "
            "tr:has-text('Drop') input[type=radio]"),
        "bid_inputs": count(
            "input[name*='faab'], input[name*='bid'], input[type='number']"),
        "buttons": labels,
    }


def do_claim_probe(pw, page, name):
    """Open the claim form for one player and report it. Submits nothing."""
    control = add_control(page, name)
    if control is None or not control.count():
        print()
        print(f"No Add control next to {name!r} on this page.")
        print("He may be rostered, or on waivers already, or the name may")
        print("not match Yahoo's spelling. Try --player with a surname.")
        return 1
    print()
    print(f"Clicking Add next to {name!r}. This opens the form; it does not")
    print("place a claim - nothing that says submit or confirm is touched.")
    try:
        control.click(timeout=10000)
        page.wait_for_timeout(3000)
    except Exception as exc:
        print(f"  the click did not land: {type(exc).__name__}: {exc}")
        return 1
    print()
    print(describe_claim(look_at_claim(page)))
    print()
    print("No claim was placed. Close the tab or navigate away.")
    return 0


def do_probe(pw, league_key, player=None):
    """Open the real page and say what is on it. Clicks nothing."""
    url = players_url(league_key)
    if not url:
        print(f"{league_key!r} is not a Yahoo league key.")
        print("They look like 470.l.715420 - python3 yahoo_client.py "
              "prints yours.")
        return 1

    # Attach only. browser_context() falls back to launching a throwaway
    # profile when nothing is listening, and that profile is signed into
    # nothing - so it reports "not signed in to Yahoo" about a browser you
    # have never seen, while your actual Chrome sits there logged in. That
    # is exactly the confusion the Sleeper side was fixed for, and reusing
    # the helper brought it straight back.
    alive, saw = submitter.cdp_probe()
    if not alive:
        print("No Chrome is listening on the debugging port, so there is no")
        print(f"signed-in browser to attach to ({saw}).")
        print()
        print("Start one, sign in to Yahoo there, leave it open, and re-run:")
        print("    python3 submitter.py --login")
        return submitter.NEEDS_LOGIN
    print(f"Attached to {saw}")
    ctx = submitter.attach(pw)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    print(f"Opening {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(3000)

    title = ""
    try:
        title = page.title()
    except Exception:
        pass
    if signed_out(page.url, title):
        print()
        # Say where it actually ended up. "You are not signed in" about a
        # page whose address you cannot see is unarguable-with, and the
        # address is the whole evidence for the claim.
        print(f"Landed on {page.url[:100]}")
        print(f"Title: {title[:70]}")
        print()
        print("That is the Yahoo login page, so the attached Chrome is not")
        print("signed in to Yahoo. Sign in in that window and run this again.")
        print("Nothing here will try to log in for you - Yahoo challenges")
        print("automated logins, and it is right to.")
        return submitter.NEEDS_LOGIN

    print(f"  page                {title[:70]}")
    print(describe(look(page)))
    if player:
        return do_claim_probe(pw, page, player)
    print()
    print("Nothing was clicked. To see what the claim form wants, name a")
    print("free agent from that list:")
    print("    python3 yahoo_submitter.py --probe LEAGUE_KEY --player SURNAME")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--probe", metavar="LEAGUE_KEY",
                    help="open the waiver page and report what is on it")
    ap.add_argument("--player", metavar="NAME",
                    help="also open that player's claim form and report it")
    args = ap.parse_args()
    if not args.probe:
        ap.print_help()
        return 1
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed here:")
        print("    python3 -m pip install playwright")
        return 1
    with sync_playwright() as pw:
        return do_probe(pw, args.probe, args.player)


if __name__ == "__main__":
    sys.exit(main())
