#!/usr/bin/env python3
"""
Place approved waiver claims in Sleeper, then prove they landed.

    python3 submitter.py pradm7 --login          # log in once, by hand
    python3 submitter.py pradm7                  # DRY RUN (default)
    python3 submitter.py pradm7 --submit         # actually place claims
    python3 submitter.py pradm7 --audit          # what is queued right now
    python3 submitter.py pradm7 --inspect LEAGUE # open a page to read selectors

WHAT IT WILL AND WILL NOT DO
It reads only rows a person approved on the page and that have not been
submitted. Everything else is invisible to it. Before each claim it
re-checks the live league: a player claimed by someone else since Tuesday
morning, a drop that left your roster, a bid that no longer fits the budget,
or a drop that is now a starter all stop that claim. After each claim it
re-reads the league through the public API and records whether a matching
pending claim actually exists — the browser's own opinion of success is not
taken as proof.

Dry run is the default, and it stops short of the final confirm.

WHY A BROWSER
Sleeper publishes no write API, so claims go through the site the way a
person would. The session lives in a browser profile on this machine that
you log into by hand once; no password is ever stored or typed by this code.

Waiver claims queue until processing (Wednesday overnight), so anything
placed here stays visible and cancellable in Sleeper until then. Audit
before it processes.
"""

import argparse
import glob
import json
import sys
from pathlib import Path

import claim_safety as cs
import sleeper_client as sc
import store as st

HERE = Path(__file__).resolve().parent
PROFILE_DIR = HERE / ".browser-profile"
SHOTS = HERE / "logs" / "claims"
SELECTORS = HERE / "selectors.json"


def load_selectors():
    return json.loads(SELECTORS.read_text())["sleeper"]


def chromium_path():
    """Use a preinstalled Chromium when Playwright's own build is absent."""
    for pattern in ("/opt/pw-browsers/**/chrome",
                    "/opt/pw-browsers/**/headless_shell"):
        hits = glob.glob(pattern, recursive=True)
        if hits:
            return hits[0]
    return None


def browser_context(pw, headed=True):
    """A persistent context, so the login you do by hand survives runs."""
    PROFILE_DIR.mkdir(exist_ok=True)
    kwargs = dict(user_data_dir=str(PROFILE_DIR), headless=not headed,
                  viewport={"width": 1280, "height": 900})
    exe = chromium_path()
    if exe:
        kwargs["executable_path"] = exe
    return pw.chromium.launch_persistent_context(**kwargs)


def do_login(pw):
    ctx = browser_context(pw, headed=True)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://sleeper.com/login", wait_until="domcontentloaded")
    print("A browser window is open. Log in to Sleeper there.")
    print("The session is saved to .browser-profile and reused from now on.")
    input("Press Enter here once you are logged in and can see your leagues... ")
    ctx.close()
    print("Saved.")


def do_inspect(pw, league_id):
    sel = load_selectors()
    ctx = browser_context(pw, headed=True)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    url = sel["players_url"].format(league_id=league_id)
    page.goto(url, wait_until="domcontentloaded")
    print(f"Opened {url}")
    print("Use devtools to find the real selectors, then edit selectors.json.")
    input("Press Enter to close... ")
    ctx.close()


def place_claim(page, sel, proposal, dry_run=True):
    """Drive the UI for one claim. Returns (ok, detail)."""
    league_id = proposal["league_id"]
    add_name = clean_name(proposal["add_player_name"])
    drop_name = clean_name(proposal["drop_player_name"])
    shot = SHOTS / f"proposal-{proposal['id']}.png"
    SHOTS.mkdir(parents=True, exist_ok=True)

    try:
        page.goto(sel["players_url"].format(league_id=league_id),
                  wait_until="domcontentloaded", timeout=30000)
        page.fill(sel["search_box"], add_name, timeout=15000)
        page.wait_for_timeout(1200)

        row = sel["player_row"].format(player_name=add_name)
        page.click(row, timeout=15000)
        page.wait_for_timeout(600)
        page.click(sel["claim_button"], timeout=15000)
        page.wait_for_timeout(800)

        if drop_name:
            try:
                page.select_option(sel["drop_select"], label=drop_name,
                                   timeout=8000)
            except Exception:
                page.click(sel["drop_option"].format(drop_name=drop_name),
                           timeout=8000)
        if proposal["bid"] is not None:
            page.fill(sel["bid_input"], str(proposal["bid"]), timeout=8000)

        page.screenshot(path=str(shot))
        if dry_run:
            return True, (f"DRY RUN — form filled but not confirmed "
                          f"(screenshot {shot.name})")

        page.click(sel["confirm_button"], timeout=15000)
        page.wait_for_timeout(2000)
        page.screenshot(path=str(shot))
        return True, f"confirmed in the UI (screenshot {shot.name})"
    except Exception as exc:
        try:
            page.screenshot(path=str(shot))
        except Exception:
            pass
        return False, f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"


def clean_name(label):
    """'Dylan Sampson (CLE RB)' -> 'Dylan Sampson' for searching."""
    if not label:
        return ""
    return str(label).split("(")[0].split("[")[0].strip()


def run(conn, user_id, week, dry_run, limit):
    rows = st.approved_unsubmitted(conn)
    if not rows:
        print("Nothing approved and waiting. Approve proposals on the page first.")
        return 0
    print(f"{len(rows)} approved claim(s) waiting.")

    checked = []
    for r in rows[:limit] if limit else rows:
        ok, why = cs.preflight(r, user_id)
        mark = "ok   " if ok else "BLOCK"
        print(f"  {mark} {r['league_name']}: ADD {clean_name(r['add_player_name'])}"
              f" / DROP {clean_name(r['drop_player_name'])} bid {r['bid']}")
        if not ok:
            print(f"        {why}")
            st.log(conn, "preflight_blocked", why, r["id"])
            conn.commit()
        else:
            checked.append(r)
    if not checked:
        print("\nNothing passed pre-flight; nothing to place.")
        return 1

    from playwright.sync_api import sync_playwright
    sel = load_selectors()
    placed = 0
    with sync_playwright() as pw:
        ctx = browser_context(pw, headed=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for r in checked:
            print(f"\n{clean_name(r['add_player_name'])} in {r['league_name']}:")
            ok, detail = place_claim(page, sel, r, dry_run=dry_run)
            print(f"  {detail}")
            if dry_run:
                st.log(conn, "dry_run", detail, r["id"])
                conn.commit()
                continue
            if not ok:
                st.mark_submitted(conn, r["id"], False, detail)
                continue
            # The browser says it worked; the league is what decides.
            found, vdetail = cs.verify_submitted(conn, r, user_id, week)
            print(f"  verification: {vdetail}")
            st.mark_submitted(conn, r["id"], found,
                              f"{detail}; {vdetail}")
            placed += 1 if found else 0
        ctx.close()

    if dry_run:
        print("\nDry run only. Nothing was submitted. Re-run with --submit "
              "once the screenshots look right.")
    else:
        print(f"\n{placed} claim(s) confirmed present in their leagues.")
        print("They stay pending until Sleeper processes waivers, so you can "
              "review or cancel them in the app until then.")
    return 0


def do_audit(conn, user_id, week):
    report = cs.audit(conn, user_id, week)
    if not report:
        print("Nothing approved or submitted to audit.")
        return 0
    print(f"Week {week} — what is actually queued in your leagues:\n")
    for row in report:
        flag = "LIVE   " if row["live"] else "MISSING"
        print(f"  {flag} [{row['league']}] ADD {clean_name(row['add'])}"
              f" / DROP {clean_name(row['drop'])}  bid {row['bid']}")
        print(f"          our status: {row['status']} — {row['detail']}")
    missing = [r for r in report if r["status"] == st.SUBMITTED and not r["live"]]
    if missing:
        print(f"\n{len(missing)} claim(s) we recorded as submitted are NOT "
              "queued in the league. Check Sleeper directly.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("username", help="your Sleeper username")
    ap.add_argument("--submit", action="store_true",
                    help="actually place claims (default is a dry run)")
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--inspect", metavar="LEAGUE_ID")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--db", default=str(st.DB_PATH))
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is needed for the browser steps:")
        print("  pip3 install playwright && python3 -m playwright install chromium")
        return 1

    if args.login:
        with sync_playwright() as pw:
            do_login(pw)
        return 0
    if args.inspect:
        with sync_playwright() as pw:
            do_inspect(pw, args.inspect)
        return 0

    try:
        state = sc.current_state()
        week = state.get("week") or 1
        user = sc.resolve_user(args.username)
    except sc.SleeperError as e:
        print(f"ERROR: {e}")
        return 1

    conn = st.connect(args.db)
    try:
        if args.audit:
            return do_audit(conn, user["user_id"], week)
        return run(conn, user["user_id"], week, not args.submit, args.limit)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
