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

import claim_order
import claim_safety as cs
import cloud_client as cloud
import localenv
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


# Chrome 111 and later refuse a WebSocket upgrade from an origin they do not
# recognise, and a CDP client is always such an origin. Without this the port
# answers /json/version perfectly and then refuses every attach.
CHROME_FLAGS = ["--remote-debugging-port=9222",
                "--remote-allow-origins=*"]


def launch_hint():
    # Quoted for a shell, where a bare * is a glob and would be expanded into
    # whatever happens to be in the current directory.
    flags = " \\\n  ".join(
        f.replace("=*", "='*'") for f in CHROME_FLAGS)
    return (f'"{CHROME_MAC}" \\\n'
            f'  {flags} \\\n'
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


def cdp_probe(timeout=0.7):
    """(alive, what we saw) for the debugging port.

    Deliberately proxy-free. urlopen honours http_proxy from the environment,
    and this URL is http://127.0.0.1 - so on a machine with a proxy set, the
    question "is Chrome listening on 9222?" was being put to the proxy, which
    is in no position to answer it and can say yes.

    Returns what it saw either way, because "nothing is listening" and "some-
    thing answered but it is not Chrome" need different fixes and looked
    identical from the outside.
    """
    import json as _json
    import urllib.request
    try:
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with direct.open(f"{CDP_URL}/json/version", timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    try:
        found = _json.loads(body).get("Browser") or "an unnamed browser"
    except ValueError:
        return False, f"something answered on 9222 but it is not Chrome: {body[:80]}"
    return True, found


def cdp_alive(timeout=0.7):
    return cdp_probe(timeout)[0]


def cdp_call(path, method="GET", timeout=5):
    """One of Chrome's own debugging endpoints, proxy-free. Parsed, or None.

    Used in preference to driving Playwright for this: a tab opened through
    Chrome's HTTP interface belongs to Chrome, and outlives the Python
    process. A page created through an attached Playwright browser belongs to
    the connection, and closing that connection can take the tab with it -
    which would shut the very window we opened for someone to sign in to.
    """
    import json as _json
    import urllib.request
    try:
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(f"{CDP_URL}{path}", method=method)
        with direct.open(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except Exception:
        return None
    try:
        return _json.loads(body)
    except ValueError:
        return body


def cdp_tabs():
    """[(target id, url)] for the real pages open in that Chrome."""
    found = cdp_call("/json/list")
    if not isinstance(found, list):
        return []
    return [(t.get("id"), t.get("url") or "") for t in found
            if t.get("type") == "page"]


def cdp_open(url):
    """Open a tab in that Chrome. Returns its target id, or None.

    Chrome wants PUT on /json/new since v111 and answers GET on older
    builds, so try the modern spelling first and fall back.
    """
    from urllib.parse import quote
    path = f"/json/new?{quote(url, safe=':/?=&')}"
    for method in ("PUT", "GET"):
        made = cdp_call(path, method=method)
        if isinstance(made, dict) and made.get("id"):
            return made["id"]
    return None


def cdp_activate(target_id):
    """Bring one tab to the front. True if Chrome said it did."""
    said = cdp_call(f"/json/activate/{target_id}")
    return said is not None


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
        print("Could not find Chrome in any of the usual places. Install it,")
        print("or launch any Chromium yourself with")
        print("  --remote-debugging-port=9222 --user-data-dir=<some dir>")
        return False
    print(f"Starting {app}")

    CHROME_PROFILE.mkdir(exist_ok=True)
    subprocess.Popen(
        [app] + CHROME_FLAGS
        + [f"--user-data-dir={CHROME_PROFILE}", "https://sleeper.com"],
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
    print("Chrome started but is not answering on port 9222 after 20s.")
    print("Either it is still coming up - give it a moment and re-run - or")
    print("the port is taken, or Chrome handed the window to an instance")
    print("already running and exited, debugging flag and all. Quitting")
    print("Chrome completely (Cmd-Q) and re-running settles which.")
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


NEEDS_LOGIN = 2          # nothing was attempted; a person has to sign in

# What the last run of the submitter would tell the page, if it could. A
# return code says whether to try again; this says what to put in front of
# the person, and "finished with problems; see the log" is not that.
_last_detail = ""


def last_detail(default=""):
    return _last_detail or default


def _note(detail):
    global _last_detail
    _last_detail = detail
    return detail


def signed_in(page, sel):
    """(True, "") when a Sleeper session is live, (False, why) when plainly not.

    Only a positive signal counts as signed out. A false alarm here stops a
    submission that would have worked, which is worse than letting a claim
    fail further in and say so.
    """
    try:
        page.goto(sel.get("home_url") or "https://sleeper.com/leagues",
                  wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
    except Exception as exc:
        return False, f"could not open sleeper.com ({type(exc).__name__})"
    if "/login" in (page.url or ""):
        return False, "Sleeper sent us to its log-in page"
    marker = sel.get("signed_out_marker")
    if marker:
        try:
            if page.locator(marker).count():
                return False, "Sleeper is showing its log-in screen"
        except Exception:
            pass  # a selector that no longer matches is not evidence
    return True, ""


def ask_for_login(why):
    """Open a Chrome that will still be there when you get to it, and say so.

    What used to happen: with nothing listening on the debugging port, a
    throwaway browser profile was launched with nobody signed into it, the
    claim failed against a logged-out Sleeper, and the window closed on the
    way out. From the outside that is a window that opens and vanishes.

    This Chrome is a detached process with a profile of its own, so it
    outlives this run and remembers the login for the next one.
    """
    print(f"Not signed in to Sleeper: {why}.")
    print()
    started = start_chrome()
    if started:
        print()
        print("Sign in as yourself in that window and leave it open.")
        print("Then press the button on the page again.")
    return (f"you are not signed in to Sleeper ({why}). A Chrome window is "
            "open on sleeper.com - sign in there, leave it open, and press "
            "the button again.")


def do_login(pw):
    """Start Chrome for the user and confirm the attach works.

    It says what it found at each step. The version of this that only said
    "start Chrome as above" pointed at a command that was never printed -
    launch_hint existed and nothing called it - so the one path where a
    person needed instructions was the path with none.
    """
    print("This tool never logs in for you: Sleeper challenges automated")
    print("logins, and a browser it starts is detectable as one. You log in")
    print("as yourself, and only the form-filling is automated.")
    print()

    alive, saw = cdp_probe()
    print(f"Port 9222: {saw}")
    if not alive:
        print()
        if start_chrome():
            return
        by_hand()
        return

    try:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        pages = [p.url for c in browser.contexts for p in c.pages]
        print(f"Attached. {len(pages)} tab(s) open:")
        for url in pages:
            print(f"    {url or '(blank)'}")
        browser.close()
    except Exception as exc:
        print(f"That Chrome would not attach: {type(exc).__name__}: {exc}")
        print()
        print("A Chrome answering on 9222 but refusing the attach was almost")
        print("certainly started without --remote-allow-origins=*, which")
        print("Chrome has required of every debugging client since v111.")
        print("Quit that Chrome (Cmd-Q) and run this again - the one it")
        print("starts carries the flag.")
        by_hand()
        return

    # Telling someone their browser is fine while they cannot see it is not
    # much use. Put a Sleeper tab in front of them.
    show_sleeper()


def show_sleeper():
    """Put a Sleeper tab in front, opening one if there is not one already."""
    tabs = cdp_tabs()
    where = sleeper_tab([url for _id, url in tabs])
    if where is None:
        made = cdp_open("https://sleeper.com")
        if not made:
            print("\nCould not open a tab in it. Open sleeper.com there "
                  "yourself.")
            return
        print("\nOpened a Sleeper tab in it.")
        cdp_activate(made)
    else:
        print("\nIt already has a Sleeper tab.")
        cdp_activate(tabs[where][0])
    print("That window should be in front now. If you still cannot see it,")
    print("look for a second Chrome in the Dock or under Cmd-Tab: it runs on")
    print("its own profile, so it is a separate icon from your everyday one.")
    print()
    print("Sign in to Sleeper there, leave the window open, then run:")
    print("    python3 submitter.py YOUR_USERNAME")


def sleeper_tab(urls):
    """Index of the first Sleeper tab in a list of tab URLs, or None."""
    for i, url in enumerate(urls):
        if "sleeper.com" in (url or "").lower():
            return i
    return None


def by_hand():
    """The command to run when we could not do it for you."""
    print()
    print("Start Chrome yourself with:")
    print()
    print("  " + launch_hint().replace("\n", "\n  "))
    print()
    print("Then sign in to Sleeper in that window, leave it open, and re-run")
    print("this to confirm the attach works.")


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

  // Who could actually be claimed from this page right now. The dedupe above
  // collapses every waiver link into one row, because they all read "W Thu" -
  // so the names have to be gathered separately or the page looks as though
  // it holds a single claimable player.
  const named = new Set();
  for (const link of document.querySelectorAll('a.player-action-button.waiver')) {
    let el = link;
    for (let i = 0; i < 8 && el; i++) {
      el = el.parentElement;
      if (!el) break;
      const n = el.querySelector('.scrolled-name');
      if (n && (n.innerText || '').trim()) {
        named.add((n.innerText || '').trim());
        break;
      }
    }
  }
  return {url: location.href, title: document.title, inputs,
          actions: uniq.filter(c => ACTION.test(c.text)),
          claimable: [...named].slice(0, 20),
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


def looks_like_league_id(value):
    """Sleeper league ids are long digit strings, and nothing else is."""
    text = str(value or "").strip()
    return text.isdigit() and len(text) >= 12


def claimable(page):
    """Players on this page that actually have a waiver link beside them."""
    try:
        return page.evaluate(PROBE_JS).get("claimable") or []
    except Exception:
        return []


def do_probe(pw, league_id, player=None, username="YOUR_USERNAME"):
    """Walk the claim flow, reporting the real controls at each step.

    A single snapshot of the players page is not enough: the claim controls
    only exist after searching for a player and opening him, so the probe has
    to take the same steps the submitter will.
    """
    if not looks_like_league_id(league_id):
        print(f"{league_id!r} is not a Sleeper league id. They are long")
        print("numbers - the one in the address bar on your league page.")
        print("Sleeper quietly redirects an unknown one to whichever league")
        print("you looked at last, so the dump would be of a league you did")
        print("not ask about, which is worse than an error.")
        return
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

    free = claimable(page)
    if not player:
        print("")
        if free:
            print("Players you can claim in this league right now:")
            for name in free:
                print("    " + name)
            print("")
            print("Re-run naming one of them to walk the claim flow:")
            print("  python3 submitter.py %s --probe %s --player '%s'"
                  % (username, league_id, free[0]))
        else:
            print("No claimable players on this page. Open the Players tab in")
            print("Sleeper and check somebody is on waivers or free.")
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
            print("the search matched nobody.")
            page.fill(sel.get("search_box"), "", timeout=8000)
            page.wait_for_timeout(2000)
            free = claimable(page)
            if free:
                print("")
                print("Names that would work here:")
                for name in free:
                    print("    " + name)
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


ROW_SHAPE_JS = r"""
(el) => {
  const chain = [];
  let node = el;
  for (let i = 0; node && i < 6; i++, node = node.parentElement) {
    chain.push({
      depth: i,
      tag: node.tagName.toLowerCase(),
      cls: (node.getAttribute('class') || '').slice(0, 80),
      cursor: getComputedStyle(node).cursor,
      text: (node.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 60),
    });
  }
  return chain;
}
"""


def drop_still_required(page):
    """Is Sleeper still asking for a drop? Visibility, not mere presence.

    The warning may be hidden rather than removed once a player is chosen, in
    which case counting DOM matches reports it forever and a successful
    selection looks like a failure.
    """
    loc = page.locator(DROP_WARNING)
    try:
        for i in range(loc.count()):
            if loc.nth(i).is_visible():
                return True
    except Exception:
        return False
    return False


def select_drop(page, sel, drop_name):
    """Pick the drop and prove it took. Returns (ok, detail).

    Selection is judged by the row itself changing - Sleeper marks the chosen
    roster row - rather than by its "select a player to drop" warning, which
    can stay on screen until the bid is actually submitted and so says nothing
    about whether a click landed.
    """
    target = sel["drop_option"].format(drop_name=drop_name)
    base = page.locator(target)
    found = base.count()
    if found != 1:
        return False, ("expected one roster row for %r in the claim dialog, "
                       "found %d" % (drop_name, found))

    row = base.first
    before = row.get_attribute("class") or ""

    def selected():
        after = row.get_attribute("class") or ""
        if after != before:
            return True, "row class changed to %r" % after
        if not drop_still_required(page):
            return True, "the drop warning cleared"
        return False, ""

    for how, action in (
            ("click", lambda: row.click(timeout=6000)),
            ("js-dispatch", lambda: row.evaluate("el => el.click()")),
            ("mouse events", lambda: row.evaluate(
                "el => ['pointerdown','mousedown','pointerup','mouseup','click']"
                ".forEach(t => el.dispatchEvent(new MouseEvent(t, "
                "{bubbles: true, cancelable: true, view: window})))")),
    ):
        try:
            row.scroll_into_view_if_needed(timeout=4000)
        except Exception:
            pass
        try:
            action()
        except Exception:
            continue
        page.wait_for_timeout(800)
        ok, why = selected()
        if ok:
            return True, "drop %s selected via %s (%s)" % (drop_name, how, why)

    try:
        chain = base.first.evaluate(ROW_SHAPE_JS)
        print("      row structure around %r:" % drop_name)
        for node in chain:
            print("        depth %d  <%s class=%r cursor=%s>  %r"
                  % (node["depth"], node["tag"], node["cls"], node["cursor"],
                     node["text"]))
    except Exception:
        pass
    return False, ("clicked the roster row for %r three ways and nothing "
                   "changed - the selection did not register" % drop_name)


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
                           "rostered, or the search found nothing" % add_name), False
        if count > 1:
            return False, ("%d waiver links visible after searching %s — "
                           "refusing to guess which player is meant"
                           % (count, add_name)), False
        page.locator(claim).first.click(timeout=15000)
        page.wait_for_timeout(1500)

        if drop_name:
            # No dropdown: the dialog lists your roster and you click one.
            # It shows full names here, unlike the abbreviated players table.
            ok, why = select_drop(page, sel, drop_name)
            if not ok:
                page.screenshot(path=str(shot))
                return False, why, False
        if proposal["bid"] is not None:
            page.fill(sel["bid_input"], str(proposal["bid"]), timeout=8000)
            page.wait_for_timeout(300)

        page.screenshot(path=str(shot))
        if dry_run:
            return True, (f"DRY RUN — form filled but not confirmed "
                          f"(screenshot {shot.name})"), False

        if not sel.get("confirm_button"):
            return False, ("confirm_button is not set in selectors.json - "
                           "run --probe and fill it in before submitting"), False
        page.click(sel["confirm_button"], timeout=15000)
        page.wait_for_timeout(2000)
        page.screenshot(path=str(shot))
        return True, f"confirmed in the UI (screenshot {shot.name})", True
    except Exception as exc:
        try:
            page.screenshot(path=str(shot))
        except Exception:
            pass
        return False, f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}", False


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


def note(conn, remote, kind, detail, row):
    """Write a local event about a claim, wherever that claim lives.

    A claim read from the hosted page carries the host's id, and this
    database's events table has a foreign key into its own proposals table.
    Writing the host's id into it is rejected outright - which crashed the
    whole run on the first blocked claim, before the others were even looked
    at. When the claim is remote the id goes in the text, where it is still
    worth having and cannot be mistaken for a local row.
    """
    if remote:
        st.log(conn, kind, f"host proposal {row['id']}: {detail}", None)
    else:
        st.log(conn, kind, detail, row["id"])
    conn.commit()


def run(conn, user_id, week, dry_run, limit, mode='auto'):
    _note("")
    remote = cloud.configured()
    if remote:
        print(f"Reading approved claims from {cloud.base_url()}")
        try:
            rows = cloud.approved_unsubmitted()
        except cloud.RemoteError as e:
            print(f"ERROR: {e}")
            return 1
    else:
        rows = st.approved_unsubmitted(conn)
    if not rows:
        print("Nothing approved and waiting. Approve proposals on the page first.")
        _note("nothing was approved, so there was nothing to place")
        return 0
    print(f"{len(rows)} approved claim(s) waiting.")

    rows = claim_order.submission_order(rows)
    fallbacks = {}
    for group in claim_order.by_league(rows).values():
        fallbacks.update(claim_order.blockers(group))

    checked = []
    for r in rows[:limit] if limit else rows:
        ok, why = cs.preflight(r, user_id)
        mark = "ok   " if ok else "BLOCK"
        print(f"  {mark} {r['league_name']}: ADD {clean_name(r['add_player_name'])}"
              f" / DROP {clean_name(r['drop_player_name'])} bid {r['bid']}")
        if r["id"] in fallbacks:
            above, reason = fallbacks[r["id"]]
            print(f"        fallback to {clean_name(above['add_player_name'])}"
                  f" ({reason})")
        if not ok:
            print(f"        {why}")
            note(conn, remote, "preflight_blocked", why, r)
        else:
            if why != "ok":
                print(f"        note: {why}")
            checked.append(r)
    if not checked:
        print("\nNothing passed pre-flight; nothing to place.")
        _note("nothing passed pre-flight - the adds or drops have moved since "
              "they were approved")
        return 1

    from playwright.sync_api import sync_playwright
    sel = load_selectors()
    placed = 0
    with sync_playwright() as pw:
        # Only ever your own Chrome. A browser this launches has no Sleeper
        # session in it and dies with the process, so falling back to one
        # gets a window that opens, fails and disappears.
        try:
            ctx = attach(pw)
        except Exception as exc:
            alive, saw = cdp_probe()
            if alive:
                why = (f"a Chrome is on 9222 ({saw}) but refused the attach "
                       "- it was started without --remote-allow-origins=*")
                print(f"{why}: {type(exc).__name__}: {exc}")
                print("Quit that Chrome and run --login again.")
            else:
                why = "no browser was attached"
                print(f"Nothing is listening on {CDP_URL} ({saw}), so there "
                      "is no signed-in browser to work in.")
            print()
            _note(ask_for_login(why))
            return NEEDS_LOGIN
        attached = True
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        ok, why = signed_in(page, sel)
        if not ok:
            _note(ask_for_login(why))
            return NEEDS_LOGIN
        # In order, and one at a time. A new claim joins the bottom of your
        # queue in the league, so the sequence they are placed in here is
        # the sequence the league works down - which is what makes the one
        # below a fallback rather than a duplicate.
        for r in checked:
            print(f"\n{clean_name(r['add_player_name'])} in {r['league_name']}:")
            # In prepare mode the form is filled but you press Confirm, so the
            # only automated action is data entry - the part where a human
            # makes mistakes - and the irreversible click stays yours.
            ok, detail, pressed = place_claim(
                page, sel, r, dry_run=dry_run or mode == "prepare")
            print(f"  {detail}")

            def record(submitted, succeeded, text, _r=r):
                if remote:
                    try:
                        cloud.report(_r["id"], submitted, succeeded, text)
                    except cloud.RemoteError as exc:
                        print(f"  ! could not report to the host: {exc}")
                elif submitted:
                    st.mark_submitted(conn, _r["id"], succeeded, text)
                else:
                    note(conn, remote, "attempt_failed", text, _r)

            if mode == "prepare" and ok:
                print("  The claim is filled in the browser. Check it, then")
                print("  click Confirm there yourself.")
                answer = input("  Pressed Confirm? [y]es / [s]kip: ").strip().lower()
                if not answer.startswith("y"):
                    record(False, False, "left unconfirmed")
                    continue
            elif dry_run:
                note(conn, remote, "dry_run", detail, r)
                continue
            elif not ok:
                # Nothing was submitted, so the claim stays approved and can
                # be retried once the cause is fixed. Retiring it here is how
                # a selector timeout silently ate a whole week's claim.
                record(False, False, detail)
                continue

            # The browser says it worked; the league is what decides.
            found, vdetail = cs.verify_submitted(conn, r, user_id, week)
            print(f"  verification: {vdetail}")
            record(True, found, f"{detail}; {vdetail}")
            placed += 1 if found else 0
        if not attached:
            ctx.close()  # never close a Chrome window the user owns

    if dry_run:
        print("\nDry run only. Nothing was submitted. Re-run with --submit "
              "once the screenshots look right.")
    else:
        _note(f"placed {placed} of {len(checked)} approved claim(s)")
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


def do_watch(args):
    """Act on the page's button, if it has been pressed.

    The page runs where there is no browser and no Sleeper session, so it
    cannot place a claim itself. It records that you asked; this runs on the
    Mac that can, checks once, and exits. Scheduled every minute, pressing
    the button is as good as running the command - and unlike a timer, every
    submission still begins with you pressing something.
    """
    import cloud_client

    if not cloud_client.configured():
        print("--watch needs FANTASY_API_URL; without a hosted page there is "
              "no button to watch.")
        return 1
    try:
        asked = cloud_client.claim_submit_request()
    except Exception as exc:
        print(f"could not reach the page: {type(exc).__name__}: {exc}")
        return 1
    if not asked.get("requested"):
        return 0

    print(f"Button pressed {asked.get('asked_at')}; placing approved claims.")
    from playwright.sync_api import sync_playwright  # noqa: F401
    state = sc.current_state()
    user = sc.resolve_user(args.username)
    conn = st.connect(args.db)
    try:
        code = run(conn, user["user_id"], sc.current_week(state),
                   False, args.limit, "auto")
        detail = last_detail(
            "placed what was approved" if code == 0 else
            "finished with problems; see logs/submit.log")
    except Exception as exc:
        code, detail = 1, f"{type(exc).__name__}: {exc}"
        print(detail)
    finally:
        conn.close()
    try:
        cloud_client.finish_submit_request(asked["id"], detail)
    except Exception as exc:
        print(f"could not report back: {type(exc).__name__}: {exc}")
    return code


def main():
    localenv.load()
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
    ap.add_argument("--retry", action="store_true",
                    help="return failed claims to the approved queue")
    ap.add_argument("--watch", action="store_true",
                    help="wait for the page's 'place them now' button and "
                         "act on it, then exit")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--db", default=str(st.DB_PATH))
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is needed for the browser steps:")
        print("  pip3 install playwright && python3 -m playwright install chromium")
        return 1

    if args.watch:
        return do_watch(args)

    if args.start_chrome:
        return 0 if start_chrome() else 1
    if args.login:
        with sync_playwright() as pw:
            do_login(pw)
        return 0
    if args.probe:
        with sync_playwright() as pw:
            do_probe(pw, args.probe, args.player, args.username)
        return 0
    if args.inspect:
        with sync_playwright() as pw:
            do_inspect(pw, args.inspect)
        return 0

    try:
        state = sc.current_state()
        week = sc.current_week(state)
        user = sc.resolve_user(args.username)
    except sc.SleeperError as e:
        print(f"ERROR: {e}")
        return 1

    conn = st.connect(args.db)
    try:
        if args.retry:
            n = st.reset_failed(conn)
            print("Returned %d failed claim(s) to the approved queue."
                  % n if n else "No failed claims to retry.")
            if n:
                print("Run again to attempt them.")
            return 0
        if args.audit:
            return do_audit(conn, user["user_id"], week)
        mode = "prepare" if args.prepare else "auto"
        return run(conn, user["user_id"], week, not (args.submit or args.prepare),
                   args.limit, mode)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
