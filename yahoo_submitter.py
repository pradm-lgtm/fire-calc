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


def player_id(player_key):
    """'470.p.12345' -> '12345', the id the claim form wants."""
    match = re.match(r"^\d+\.p\.(\d+)$", str(player_key or "").strip())
    return match.group(1) if match else None


def claim_url(league_key, player_key):
    """Straight to the claim form for one player.

    No players page in between. The API says who is available and what
    their ids are, so walking a fifty-row table to find a link is scraping
    for something an endpoint already answered - and it is the page Yahoo
    blocked when it was asked for twice in a row.
    """
    league = league_number(league_key)
    who = player_id(player_key)
    return f"{SITE}/f1/{league}/addplayer?apid={who}" if league and who else None


def matching(players, name):
    """The free agents whose name contains what you typed, case-blind."""
    wanted = str(name or "").strip().lower()
    if not wanted:
        return []
    return [p for p in players if wanted in (p.get("name") or "").lower()]


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


def describe_claim(found):
    """What the form behind Add turns out to want."""
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


def do_probe(pw, league_key, player=None):
    """Open the claim form for one player and report what it wants.

    The lookup is the API's job and the form is the browser's. Nothing here
    reads league data off a page: yahoo_client already answers who is
    available, and asking the website the same question was both redundant
    and the thing Yahoo blocked on a second visit.
    """
    if not league_number(league_key):
        print(f"{league_key!r} is not a Yahoo league key.")
        print("They look like 470.l.715420 - python3 yahoo_client.py "
              "prints yours.")
        return 1
    if not player:
        print("Name a free agent to look at the claim form for:")
        print("    python3 yahoo_submitter.py --probe LEAGUE_KEY "
              "--player SURNAME")
        print("python3 yahoo_client.py --wire LEAGUE_KEY lists who is free.")
        return 1

    import yahoo_client as yc
    try:
        wire = yc.free_agents(league_key, count=100)
    except yc.YahooError as exc:
        print(f"Could not ask Yahoo who is available: {exc}")
        return 1
    hits = matching(wire, player)
    if not hits:
        print(f"Nobody matching {player!r} is a free agent in that league.")
        print("python3 yahoo_client.py --wire LEAGUE_KEY lists who is.")
        return 1
    if len(hits) > 1:
        print(f"{player!r} matches more than one free agent:")
        for p in hits[:8]:
            print(f"    {p['name']}  ({p.get('team')} {p.get('position')})")
        print("Use enough of the name to pick one.")
        return 1

    who = hits[0]
    url = claim_url(league_key, who["key"])
    if not url:
        print(f"Could not build a claim address from {who['key']!r}.")
        return 1

    alive, saw = submitter.cdp_probe()
    if not alive:
        print("No Chrome is listening on the debugging port, so there is no")
        print(f"signed-in browser to attach to ({saw}).")
        print()
        print("Start one, sign in to Yahoo there, leave it open, and re-run:")
        print("    python3 submitter.py pradm7 --start-chrome")
        return submitter.NEEDS_LOGIN
    print(f"Attached to {saw}")

    ctx = submitter.attach(pw)
    # A tab of its own. Reusing whatever was open meant re-navigating a page
    # already sitting on that address, which is where ERR_BLOCKED_BY_RESPONSE
    # came from - and it leaves whatever you were looking at alone.
    page = ctx.new_page()
    print(f"{who['name']} ({who.get('team')} {who.get('position')})"
          f"  {who['key']}")
    print(f"Opening {url}")
    print("This opens the claim form. It does not place a claim - nothing")
    print("reading submit or confirm is touched.")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)
    except Exception as exc:
        print()
        print(f"The page would not open: {type(exc).__name__}: {exc}")
        print("If that says ERR_BLOCKED_BY_RESPONSE, Yahoo refused the")
        print("navigation rather than the tab failing - try it by hand in")
        print("that same Chrome window and see what it shows you.")
        return 1

    title = ""
    try:
        title = page.title()
    except Exception:
        pass
    if signed_out(page.url, title):
        print()
        print(f"Landed on {page.url[:100]}")
        print("That is the Yahoo login page, so that Chrome is not signed in")
        print("to Yahoo. Sign in in that window and run this again.")
        return submitter.NEEDS_LOGIN

    print()
    print(f"  page                {title[:70]}")
    print(describe_claim(look_at_claim(page)))
    print()
    print("No claim was placed. Send this and the claim flow gets written")
    print("from it.")
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
