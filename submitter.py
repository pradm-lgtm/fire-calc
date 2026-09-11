#!/usr/bin/env python3
"""
Place approved waiver claims in Sleeper, then prove they landed.

    python3 submitter.py pradm7 --login          # start Chrome, log in there
    python3 submitter.py pradm7                  # DRY RUN (default)
    python3 submitter.py pradm7 --prepare        # fill it, you press Confirm
    python3 submitter.py pradm7 --submit         # fully automatic
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


CDP_URL = "http://127.0.0.1:9222"

CHROME_MAC = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
CHROME_PROFILE = Path.home() / ".fantasy-chrome"


def launch_hint():
    return (f'"{CHROME_MAC}" \\\n'
            f'  --remote-debugging-port=9222 \\\n'
            f'  --user-data-dir="{CHROME_PROFILE}"')


def chrome_app():
    """The user's real Chrome, whatever it is called on this machine."""
    for path in (CHROME_MAC,
                 "/Applications/Chromium.app/Contents/MacOS/Chromium",
                 "/usr/bin/google-chrome", "/usr/bin/chromium",
                 "/usr/bin/chromium-browser"):
        if Path(path).exists():
            return path
    return None


def cdp_alive(timeout=0.7):
    import urllib.request
    try:
        urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=timeout)
        return True
    except Exception:
        return False


def start_chrome():
    """Launch Chrome in the background with a debugging port.

    Run detached on purpose: told to paste the raw command, you end up with a
    terminal that never returns and no obvious way forward.
    """
    import subprocess
    import time

    if cdp_alive():
        print(f"Chrome is already listening on {CDP_URL} — nothing to do.")
        return True
    app = chrome_app()
    if not app:
        print("Could not find Chrome. Install it, or launch any Chromium with")
        print("  --remote-debugging-port=9222 --user-data-dir=<some dir>")
        return False

    CHROME_PROFILE.mkdir(exist_ok=True)
    subprocess.Popen(
        [app, "--remote-debugging-port=9222",
         f"--user-data-dir={CHROME_PROFILE}", "https://sleeper.com"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    for _ in range(40):
        if cdp_alive():
            print("Chrome is running in the background, on sleeper.com.")
            print("Log in there as yourself and solve any challenge — then")
            print("leave the window open and come back here.")
            print()
            print("Next:  python3 submitter.py YOUR_USERNAME --prepare")
            return True
        time.sleep(0.5)
    print("Chrome started but is not answering on port 9222 yet. Give it a")
    print("moment and re-run, or check whether a Chrome is already running")
    print("with a different profile.")
    return False


def attach(pw):
    """Attach to a Chrome you launched and logged into yourself.

    Sleeper challenges automated logins, and rightly: a browser started by
    Playwright is identifiable. Rather than trying to look otherwise, do the
    login as a person in an ordinary Chrome window and let this attach to
    that live session afterwards. The authentication is genuinely human; only
    the form-filling is automated.
    """
    browser = pw.chromium.connect_over_cdp(CDP_URL)
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    return ctx


def browser_context(pw, headed=True):
    """Attach to your Chrome if it is listening; otherwise launch our own."""
    try:
        return attach(pw)
    except Exception:
        pass
    PROFILE_DIR.mkdir(exist_ok=True)
    kwargs = dict(user_data_dir=str(PROFILE_DIR), headless=not headed,
                  viewport={"width": 1280, "height": 900})
    exe = chromium_path()
    if exe:
        kwargs["executable_path"] = exe
    else:
        kwargs["channel"] = "chrome"  # real Chrome beats bundled Chromium
    return pw.chromium.launch_persistent_context(**kwargs)


def do_login(pw):
    """Start Chrome for the user and confirm the attach works."""
    print("This tool never logs in for you: Sleeper challenges automated")
    print("logins, and a browser it starts is detectable as one. You log in")
    print("as yourself, and only the form-filling is automated.")
    print()
    if not cdp_alive():
        start_chrome()
        return
    try:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        pages = [p.url for c in browser.contexts for p in c.pages]
        print(f"Attached to your Chrome. {len(pages)} tab(s) open.")
        print("Run the submitter now; it will reuse this session.")
        browser.close()
    except Exception:
        print("(Nothing is listening on port 9222 yet — start Chrome as above,")
        print(" then re-run this to confirm the attach works.)")


PROBE_JS = r"""
() => {
  const vis = el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const attrs = el => {
    const keep = ['id','name','type','placeholder','aria-label','role',
                  'data-testid','class'];
    const out = {};
    for (const k of keep) {
      const v = el.getAttribute(k);
      if (v) out[k] = v.length > 70 ? v.slice(0, 70) + '...' : v;
    }
    return out;
  };
  const inputs = [...document.querySelectorAll('input,textarea,select')]
    .filter(vis).map(el => ({tag: el.tagName.toLowerCase(), ...attrs(el)}));

  // Sleeper renders no <button> elements: its controls are divs that merely
  // look and behave like buttons. Find them by the pointer cursor and short
  // leaf text instead of by tag, role or class name.
  const chat = document.querySelector('[class*=chat i],[class*=Chat]');
  const clickable = [...document.querySelectorAll('div,span,a,button,li')]
    .filter(el => !(chat && chat.contains(el)))
    .filter(el => {
      if (!vis(el)) return false;
      if (getComputedStyle(el).cursor !== 'pointer') return false;
      const t = (el.innerText || '').trim();
      return t && t.length <= 40 && el.querySelectorAll('*').length <= 4;
    })
    .map(el => ({tag: el.tagName.toLowerCase(),
                 text: (el.innerText || '').trim().replace(/\s+/g, ' '),
                 ...attrs(el)}));

  const seen = new Set();
  const uniq = clickable.filter(c => {
    const k = c.tag + '|' + c.text;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
  const ACTION = /\b(confirm|submit|place|claim|cancel|done|save|bid|waiver|continue|next)\b/i;
  return {url: location.href, title: document.title, inputs,
          actions: uniq.filter(c => ACTION.test(c.text)),
          clickable: uniq.slice(0, 150)};
}
"""


def dump(page, label):
    data = page.evaluate(PROBE_JS)
    print("")
    print("=" * 66)
    print(label)
    print("=" * 66)
    print("url: " + str(data["url"]))
    print("")
    print("inputs (%d):" % len(data["inputs"]))
    for i in data["inputs"]:
        print("  %r" % (i,))
    print("")
    actions = data.get("actions") or []
    if actions:
        print("ACTION-LIKE controls (%d) - the ones that matter:" % len(actions))
        for a in actions:
            print("  %r" % (a,))
        print("")
    print("all clickable elements (%d):" % len(data["clickable"]))
    for c in data["clickable"]:
        print("  %r" % (c,))
    return data


def do_probe(pw, league_id, player=None):
    """Walk the claim flow, reporting the real controls at each step.

    A single snapshot of the players page is not enough: the claim controls
    only exist after searching for a player and opening him, so the probe has
    to take the same steps the submitter will.
    """
    sel = load_selectors()
    ctx = browser_context(pw, headed=True)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    url = sel["players_url"].format(league_id=league_id)
    print("Opening " + url)
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(4000)

    if "login" in page.url.lower():
        print("")
        print("Redirected to login — the attached Chrome is not signed in.")
        return
    dump(page, "STEP 1 - players page")

    if not player:
        print("")
        print("Re-run with a player name to walk the claim flow, e.g.")
        print("  python3 submitter.py USER --probe %s --player 'Dylan Sampson'"
              % league_id)
        return

    box = sel.get("search_box")
    try:
        page.fill(box, player, timeout=10000)
    except Exception:
        print("")
        print("Could not fill %r; trying the first visible text input." % box)
        page.fill("input[type=text]", player, timeout=10000)
    page.wait_for_timeout(2500)
    dump(page, "STEP 2 - after searching for %s" % player)

    short = abbrev_name(player)
    print("")
    print("(rows show the abbreviated name: %r)" % short)
    try:
        claim = sel.get("claim_button", "a.player-action-button.waiver")
        count = page.locator(claim).count()
        print("waiver links on the page: %d" % count)
        if count == 0:
            print("No waiver link for this player — he may be rostered, or")
            print("the search returned nothing.")
        else:
            page.locator(claim).first.click(timeout=8000)
            page.wait_for_timeout(2500)
            dump(page, "STEP 3 - claim dialog")
    except Exception as exc:
        print("")
        print("Could not open the claim dialog: %s"
              % str(exc).splitlines()[0][:120])

    shot = HERE / "logs" / "probe-flow.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(shot))
    print("")
    print("Screenshot: " + str(shot))
    print("Paste all of the above and I can write selectors.json.")


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


DROP_WARNING = "text=/please select a player to drop/i"


def select_drop(page, sel, drop_name):
    """Pick the drop, and prove it took. Returns (ok, detail).

    Clicking the player's name may only open his detail view, so the click is
    retried up the ancestor chain until Sleeper's "select a player to drop"
    warning clears. That warning disappearing is the only reliable evidence
    the selection registered - without checking it, a claim can reach the
    submit button with no drop chosen at all.
    """
    if page.locator(DROP_WARNING).count() == 0:
        return True, "no drop needed (roster has space)"

    target = sel["drop_option"].format(drop_name=drop_name)
    base = page.locator(target)
    found = base.count()
    if found != 1:
        return False, ("expected one roster row for %r in the claim dialog, "
                       "found %d" % (drop_name, found))

    attempts = [("the name cell", base.first)]
    for depth in range(1, 4):
        attempts.append(("ancestor %d" % depth,
                         base.first.locator("xpath=" + "/".join([".."] * depth))))

    for label, loc in attempts:
        # A normal click lands at the element's centre, which on a row lands
        # on the name cell inside it - and that child may stop the event. So
        # try the real click first, then dispatch one directly on the element,
        # which fires its handler regardless of what sits on top.
        for how, action in (("click", lambda l=loc: l.click(timeout=4000)),
                            ("js-dispatch",
                             lambda l=loc: l.evaluate("el => el.click()"))):
            try:
                action()
            except Exception:
                continue
            page.wait_for_timeout(700)
            if page.locator(DROP_WARNING).count() == 0:
                return True, ("drop %s selected via %s on %s"
                              % (drop_name, how, label))
    return False, ("clicked %r but Sleeper still says a drop is needed - the "
                   "selection did not register" % drop_name)


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

        # Searching narrows the table to one player, so the single waiver
        # link on the page is his. Clicking the row itself does nothing
        # useful; the link in the row is what opens the claim.
        claim = sel["claim_button"]
        count = page.locator(claim).count()
        if count == 0:
            return False, ("no waiver link for %s — he may already be "
                           "rostered, or the search found nothing" % add_name)
        if count > 1:
            return False, ("%d waiver links visible after searching %s — "
                           "refusing to guess which player is meant"
                           % (count, add_name))
        page.locator(claim).first.click(timeout=15000)
        page.wait_for_timeout(1500)

        if drop_name:
            # No dropdown: the dialog lists your roster and you click one.
            # It shows full names here, unlike the abbreviated players table.
            ok, why = select_drop(page, sel, drop_name)
            if not ok:
                page.screenshot(path=str(shot))
                return False, why
        if proposal["bid"] is not None:
            page.fill(sel["bid_input"], str(proposal["bid"]), timeout=8000)
            page.wait_for_timeout(300)

        page.screenshot(path=str(shot))
        if dry_run:
            return True, (f"DRY RUN — form filled but not confirmed "
                          f"(screenshot {shot.name})")

        if page.locator(DROP_WARNING).count() > 0:
            page.screenshot(path=str(shot))
            return False, ("refusing to submit: Sleeper still says a drop is "
                           "required")
        if not sel.get("confirm_button"):
            return False, ("confirm_button is not set in selectors.json - "
                           "run --probe and fill it in before submitting")
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


def abbrev_name(full):
    """'Terrance Ferguson' -> 'T. Ferguson', the form Sleeper shows in rows."""
    parts = [x for x in str(full or "").split() if x]
    if len(parts) < 2:
        return str(full or "")
    return "%s. %s" % (parts[0][0], " ".join(parts[1:]))


def clean_name(label):
    """'Dylan Sampson (CLE RB)' -> 'Dylan Sampson' for searching."""
    if not label:
        return ""
    return str(label).split("(")[0].split("[")[0].strip()


def run(conn, user_id, week, dry_run, limit, mode='auto'):
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
        attached = ctx.browser is not None and not str(
            getattr(ctx, "_user_data_dir", "") or "")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for r in checked:
            print(f"\n{clean_name(r['add_player_name'])} in {r['league_name']}:")
            # In prepare mode the form is filled but you press Confirm, so the
            # only automated action is data entry - the part where a human
            # makes mistakes - and the irreversible click stays yours.
            ok, detail = place_claim(page, sel, r,
                                     dry_run=dry_run or mode == "prepare")
            print(f"  {detail}")

            if mode == "prepare" and ok:
                print("  The claim is filled in the browser. Check it, then")
                print("  click Confirm there yourself.")
                answer = input("  Pressed Confirm? [y]es / [s]kip: ").strip().lower()
                if not answer.startswith("y"):
                    st.log(conn, "skipped", "left unconfirmed", r["id"])
                    conn.commit()
                    continue
            elif dry_run:
                st.log(conn, "dry_run", detail, r["id"])
                conn.commit()
                continue
            elif not ok:
                st.mark_submitted(conn, r["id"], False, detail)
                continue

            # The browser says it worked; the league is what decides.
            found, vdetail = cs.verify_submitted(conn, r, user_id, week)
            print(f"  verification: {vdetail}")
            st.mark_submitted(conn, r["id"], found, f"{detail}; {vdetail}")
            placed += 1 if found else 0
        if not attached:
            ctx.close()  # never close a Chrome window the user owns

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
                    help="place claims fully automatically")
    ap.add_argument("--prepare", action="store_true",
                    help="fill each claim and let you press Confirm (recommended)")
    ap.add_argument("--login", action="store_true",
                    help="start Chrome for login and confirm the attach")
    ap.add_argument("--start-chrome", action="store_true",
                    help="just launch the browser and exit")
    ap.add_argument("--inspect", metavar="LEAGUE_ID",
                    help="open the page so you can read selectors yourself")
    ap.add_argument("--probe", metavar="LEAGUE_ID",
                    help="dump the page's real inputs and clickable controls")
    ap.add_argument("--player", metavar="NAME",
                    help="with --probe, walk the claim flow for this player")
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

    if args.start_chrome:
        return 0 if start_chrome() else 1
    if args.login:
        with sync_playwright() as pw:
            do_login(pw)
        return 0
    if args.probe:
        with sync_playwright() as pw:
            do_probe(pw, args.probe, args.player)
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
        mode = "prepare" if args.prepare else "auto"
        return run(conn, user["user_id"], week, not (args.submit or args.prepare),
                   args.limit, mode)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
