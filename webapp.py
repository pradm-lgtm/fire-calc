#!/usr/bin/env python3
"""
Approval page for waiver proposals.

    python3 webapp.py                 # http://127.0.0.1:8777
    python3 webapp.py --host 0.0.0.0  # reachable over Tailscale, phone-friendly

Stdlib only, so there is nothing to install and nothing to keep running but
this one process. Every control is a plain form POST: no JavaScript, which
means it works on a phone browser, over a flaky connection, with the back
button, exactly as you would expect.

This page decides nothing. It shows what the weekly job proposed and records
what you chose. The submitter is a separate process that only ever reads rows
you approved here.

Article text is quoted verbatim on this page, so everything is HTML-escaped:
it comes from web pages and is not to be trusted as markup.
"""

import argparse
import html
import json
import os
import re
import threading
import traceback
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import cloud_auth as auth
import db
import localenv
from lineup import SLOT_ELIGIBILITY
import store as st

CSS = """
:root { color-scheme: light dark; --bg:#f4f5f7; --card:#fff; --ink:#12141a;
        --muted:#5c6370; --line:#e3e6ea; --ok:#0a7d28; --ok-ink:#fff;
        --no:#b3261e; --accent:#1a56db;
        --qb:#7c3aed; --rb:#0a7d28; --wr:#1a56db; --te:#c2410c; --def:#475569; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0f1115; --card:#181b21; --ink:#e8eaed; --muted:#9aa2ad;
          --line:#272b33; --ok:#15803d; --ok-ink:#fff; --no:#f87171;
          --accent:#7aa2f7;
          --qb:#a78bfa; --rb:#4ade80; --wr:#7aa2f7; --te:#fb923c; --def:#94a3b8; }
}
* { box-sizing:border-box; -webkit-text-size-adjust:100%; }
body { margin:0; padding:14px; background:var(--bg); color:var(--ink);
       font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
.wrap { max-width:720px; margin:0 auto; }
h1 { font-size:21px; margin:0 0 4px; letter-spacing:-.01em; }
.sub { color:var(--muted); font-size:13px; margin-bottom:18px; }
h2 { font-size:17px; margin:24px 0 8px; display:flex; justify-content:space-between;
     align-items:baseline; gap:8px; }
.meta { color:var(--muted); font-size:12px; font-weight:normal; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px;
        padding:14px; margin-bottom:12px; }
.move { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.move + .move { margin-top:6px; }
.tag { font-size:10px; font-weight:800; letter-spacing:.06em; padding:3px 6px;
       border-radius:4px; background:var(--line); color:var(--muted); }
.pos { font-size:10px; font-weight:800; letter-spacing:.04em; padding:3px 6px;
       border-radius:4px; color:#fff; }
.pos.QB{background:var(--qb)} .pos.RB{background:var(--rb)}
.pos.WR{background:var(--wr)} .pos.TE{background:var(--te)}
.pos.DEF,.pos.K{background:var(--def)}
.add { font-weight:650; font-size:17px; }
.drop { color:var(--muted); }
.why { color:var(--muted); font-size:13px; margin:10px 0 0; }
.quote { border-left:3px solid var(--line); padding-left:10px; margin:8px 0 0;
         color:var(--muted); font-size:13px; font-style:italic; }
form.row { display:flex; gap:8px; align-items:center; margin-top:14px;
           flex-wrap:wrap; }
.bidwrap { display:flex; align-items:center; gap:6px; }
input[type=number] { width:84px; min-height:46px; padding:10px; font-size:16px;
                     border:1px solid var(--line); border-radius:8px;
                     background:var(--bg); color:var(--ink); }
button { min-height:46px; padding:10px 18px; border-radius:8px;
         border:1px solid transparent; font-weight:650; cursor:pointer;
         font-size:16px; touch-action:manipulation; }
.approve { background:var(--ok); color:var(--ok-ink); flex:1; }
.decline { background:transparent; color:var(--no); border-color:var(--line);
           flex:1; }
.state { font-size:14px; font-weight:650; margin:12px 0 0; }
.state.approved { color:var(--ok); } .state.declined { color:var(--no); }
.state.submitted { color:var(--accent); }
.empty { color:var(--muted); padding:24px 0; }
.bar { background:var(--card); border:1px solid var(--line); border-radius:8px;
       padding:9px 12px; font-size:13px; color:var(--muted); margin-bottom:10px; }
.warn { color:var(--no); font-weight:650; }
.counts { display:flex; gap:14px; font-size:13px; color:var(--muted);
          margin-bottom:16px; flex-wrap:wrap; }
.counts b { color:var(--ink); }
nav { display:flex; gap:6px; margin-bottom:14px; }
nav a { flex:1; text-align:center; padding:9px 8px; border-radius:8px;
        border:1px solid var(--line); background:var(--card); font-size:14px;
        font-weight:650; text-decoration:none; color:var(--muted); }
nav a.on { background:var(--accent); border-color:var(--accent); color:#fff; }
.slot { display:flex; align-items:center; gap:10px; padding:11px 12px;
        background:var(--card); border:1px solid var(--line);
        border-radius:10px; margin-bottom:7px; border-left-width:4px; }
.slot.GREEN { border-left-color:var(--ok); }
.slot.YELLOW { border-left-color:#b58900; }
.slot.RED { border-left-color:var(--no); }
.slot.UNKNOWN { border-left-color:var(--line); }
.slotname { font-size:10px; font-weight:800; letter-spacing:.06em;
            color:var(--muted); width:44px; flex:none; }
.who { font-weight:650; flex:1; min-width:0; }
.rankt { color:var(--muted); font-size:12px; text-align:right; flex:none; }
.advice { font-size:13px; color:var(--muted); margin:6px 0 0; }
.player { display:flex; align-items:center; gap:10px; padding:9px 2px; }
.player + .player { border-top:1px solid var(--line); }
.face { width:34px; height:34px; border-radius:50%; flex:none;
        background:var(--line) center/cover no-repeat; }
.player .who { display:flex; flex-direction:column; min-width:0; flex:1; }
.pname { font-weight:650; line-height:1.25; }
.under { color:var(--muted); font-size:12px; }
.player.starting .face { width:46px; height:46px; }
.player.starting .pname { font-size:18px; }
.decide { background:var(--card); border:1px solid var(--line);
          border-left-width:4px; border-radius:12px; padding:12px 14px;
          margin-bottom:12px; }
.decide.RED { border-left-color:var(--no); }
.decide.YELLOW { border-left-color:#b58900; }
.decidehead { display:flex; justify-content:space-between; align-items:baseline;
              gap:8px; font-size:12px; color:var(--muted);
              text-transform:uppercase; letter-spacing:.05em; font-weight:700; }
.verdict.RED { color:var(--no); } .verdict.YELLOW { color:#b58900; }
.alts { margin-top:10px; padding-top:4px; border-top:1px solid var(--line); }
.altlabel { display:block; font-size:11px; font-weight:800; letter-spacing:.06em;
            text-transform:uppercase; color:var(--muted); margin:10px 0 2px; }
.fold { background:var(--card); border:1px solid var(--line);
        border-radius:12px; padding:4px 14px 10px; margin-bottom:10px; }
.fold > summary { display:flex; justify-content:space-between; gap:8px;
                  align-items:baseline; padding:10px 0; cursor:pointer;
                  font-weight:650; list-style:none; }
.fold > summary::-webkit-details-marker { display:none; }
.fold > summary::after { content:'▸'; color:var(--muted); font-weight:400; }
.fold[open] > summary::after { content:'▾'; }
label { font-size:13px; color:var(--muted); }
@media (max-width:520px) {
  body { padding:10px; }
  .add { font-size:16px; }
  form.row { gap:6px; }
  .bidwrap { width:100%; }
  input[type=number] { flex:1; width:auto; }
  .approve, .decline { flex:1 1 45%; }
}
"""


# A connection string carries its password; an error quoting one must not.
_CREDENTIALS = re.compile(r"://[^/@\s]+@")


def scrub(text):
    return _CREDENTIALS.sub("://...@", str(text))


def e(v):
    return html.escape("" if v is None else str(v), quote=True)


LOGIN_HTML = """<form method='post' action='/login' class='card'
      style='max-width:340px;margin:12vh auto'>
  <h1>Waiver proposals</h1>
  <p class='why'>%s</p>
  <input type='password' name='password' placeholder='Password'
         autofocus autocomplete='current-password'
         style='width:100%%;min-height:46px;padding:10px;font-size:16px;
                border:1px solid var(--line);border-radius:8px;
                background:var(--bg);color:var(--ink);margin-bottom:10px'>
  <button class='approve' style='width:100%%'>Sign in</button>
</form>"""


def claims_json(conn, include_submitted=False):
    """What the submitter may see: approved and awaiting submission.

    With include_submitted, recently submitted ones come too - the audit
    needs them to tell "we placed this" from "the league has this".
    """
    if include_submitted:
        run = st.latest_run(conn)
        rows = [r for r in st.proposals_for_run(conn, run["id"])
                if r["status"] in (st.APPROVED, st.SUBMITTED)] if run else []
    else:
        rows = st.approved_unsubmitted(conn)
    return [{
        "id": r["id"], "platform": r["platform"],
        "league_id": r["league_id"], "league_name": r["league_name"],
        "add_player_id": r["add_player_id"],
        "add_player_name": r["add_player_name"],
        "add_position": r["add_position"],
        "drop_player_id": r["drop_player_id"],
        "drop_player_name": r["drop_player_name"],
        "drop_position": r["drop_position"],
        "bid": r["bid"], "max_bid": r["max_bid"],
        "status": r["status"],
    } for r in rows]


def render(conn):
    run = st.latest_run(conn)
    if not run:
        return page(nav("/") + "<h1>Waiver proposals</h1>"
                    + refresh_button("Work out this week's moves")
                    + "<p class='empty'>Nothing yet. The weekly job runs "
                      "Monday night, after the last game.</p>", "Waivers")

    rows = st.proposals_for_run(conn, run["id"])
    if not rows:
        return page(nav("/") + "<h1>Waiver proposals</h1>"
                    + refresh_button("Look again")
                    + "<p class='empty'>That run produced no proposals.</p>",
                    "Waivers")

    by_league = {}
    for r in rows:
        by_league.setdefault((r["league_id"], r["league_name"]), []).append(r)

    tally = {}
    for r in rows:
        tally[r["status"]] = tally.get(r["status"], 0) + 1
    counts = " ".join(
        f"<span><b>{tally.get(k, 0)}</b> {label}</span>"
        for k, label in ((st.PENDING, "to review"), (st.APPROVED, "approved"),
                         (st.DECLINED, "declined"), (st.SUBMITTED, "submitted"))
        if tally.get(k))

    out = [nav("/"),
           f"<h1>Waiver proposals</h1>"
           f"<div class='sub'>Week {e(run['week'])} &middot; filed "
           f"{e(said_ago(age_of(run)))} &middot; nothing is submitted until "
           f"you approve it</div>"
           f"<div class='counts'>{counts}</div>",
           refresh_button("Work them out again")]

    for (lid, lname), items in by_league.items():
        budget = items[0]["max_bid"] or 0
        committed = st.budget_committed(conn, lid)
        bar = (f"FAAB committed here: <strong>{committed}</strong>"
               f"{f' of {budget}' if budget else ''}")
        if budget and committed > budget:
            bar += " <span class='warn'>— over budget</span>"
        pending = sum(1 for i in items if i["status"] == st.PENDING)
        out.append(f"<h2><span>{e(lname)}</span><span class='meta'>"
                   f"{pending} of {len(items)} to review</span></h2>"
                   f"<div class='bar'>{bar}</div>")
        for r in items:
            out.append(card(r))
    return page("".join(out), "Waiver proposals")


def card(r):
    srcs = json.loads(r["sources"] or "[]")
    src_txt = ", ".join(short_source(s) for s in srcs) or "—"
    bits = [f"<div class='card'>",
            "<div class='move'>",
            "<span class='tag'>ADD</span>",
            pos_chip(r["add_position"]),
            f"<span class='add'>{e(strip_paren(r['add_player_name']))}</span>",
            "</div>"]
    if r["drop_player_name"]:
        bits += ["<div class='move'>",
                 "<span class='tag'>DROP</span>",
                 pos_chip(r["drop_position"]),
                 f"<span class='drop'>{e(strip_paren(r['drop_player_name']))}</span>",
                 "</div>"]
    bits.append(f"<p class='why'>{e(r['consensus'])} source(s): {e(src_txt)}"
                + (f" &middot; {e(r['rationale'])}" if r["rationale"] else "")
                + "</p>")
    if r["quote"]:
        bits.append(f"<p class='quote'>{e(r['quote'])}</p>")

    if r["status"] in (st.PENDING,):
        bits.append(
            "<form class='row' method='post' action='/decide'>"
            f"<input type='hidden' name='id' value='{e(r['id'])}'>"
            "<span class='bidwrap'><label>Bid</label>"
            f"<input type='number' name='bid' inputmode='numeric' min='0'"
            f" max='{e(r['max_bid'] or 100)}'"
            f" value='{e(r['bid'] if r['bid'] is not None else 0)}'></span>"
            "<button class='approve' name='action' value='approve'>Approve</button>"
            "<button class='decline' name='action' value='decline'>Decline</button>"
            "</form>")
    else:
        label = {st.APPROVED: "Approved", st.DECLINED: "Declined",
                 st.SUBMITTED: "Submitted", st.FAILED: "Submission failed"}.get(
                     r["status"], r["status"])
        extra = f" &middot; bid {r['bid']}" if r["bid"] is not None else ""
        line = (f"<p class='state {e(r['status'])}'>{e(label)}{extra}</p>")
        if r["result"]:
            line += f"<p class='why'>{e(r['result'])}</p>"
        if r["status"] in (st.APPROVED, st.DECLINED):
            line += ("<form class='row' method='post' action='/decide'>"
                     f"<input type='hidden' name='id' value='{e(r['id'])}'>"
                     "<button class='decline' name='action' value='reopen'>"
                     "Undo</button></form>")
        bits.append(line)
    bits.append("</div>")
    return "".join(bits)


def pos_chip(pos):
    """Position badge. Colour is the fastest thing to scan on a phone."""
    p = (pos or "").upper()
    cls = p if p in ("QB", "RB", "WR", "TE", "DEF", "K") else ""
    return f"<span class='pos {cls}'>{e(p or '?')}</span>"


def strip_paren(name):
    """'Dylan Sampson (CLE RB)' -> 'Dylan Sampson' - the chip carries position."""
    text = str(name or "")
    if "(" in text:
        head, _, tail = text.partition("(")
        keep = head.strip()
        # Preserve an injury flag, which matters to the decision.
        if "[" in tail:
            keep += " " + tail[tail.index("["):].strip()
        return keep
    return text


def short_source(url):
    try:
        host = urlparse(url).netloc or url
        return host.replace("www.", "")
    except Exception:
        return str(url)


def nav(here):
    tabs = (("/", "Waivers"), ("/lineup", "Start / sit"))
    return "<nav>" + "".join(
        f"<a class='{'on' if path == here else ''}' href='{path}'>{label}</a>"
        for path, label in tabs) + "</nav>"


# Green is not a compliment and red is not an order: these compare one
# lineup against one site's consensus, which is a second opinion, not a
# verdict.
VERDICT_NOTE = {
    "RED": "well off consensus",
    "YELLOW": "close, worth a look",
    "UNKNOWN": "not in the rankings",
}


# How old a set of verdicts may be before the page works them out again.
# Lineups change between opening the page and kickoff, and advice about a
# lineup you have since changed is worse than none - but this runs inside a
# request, so re-running it on every reload would make the page unusable.
LINEUP_MAX_AGE = timedelta(hours=3)


def age_of(check):
    if not check or not check["created_at"]:
        return None
    try:
        when = datetime.fromisoformat(check["created_at"])
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - when


def said_ago(age):
    if age is None:
        return "just now"
    minutes = int(age.total_seconds() // 60)
    if minutes < 2:
        return "just now"
    if minutes < 60:
        return f"{minutes} minutes ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    days = hours // 24
    return f"{days} day{'s' if days > 1 else ''} ago"


def refresh_waivers(conn, db_path):
    """Work this week's proposals out again. Returns an error, or None.

    Unlike the start/sit check this never happens on its own. A new run is a
    new set of proposals, and the page shows the newest, so refreshing while
    you are part way through reviewing would move the list out from under
    you. Anything already approved still submits: the submitter reads
    approved-and-unsubmitted across every run, not just the latest.
    """
    import run_weekly

    username = os.environ.get("FANTASY_USER", "")
    if not username:
        return "FANTASY_USER is not set on the host, so I cannot look up " \
               "your leagues."
    try:
        run_weekly.main_for(username, db_path, force=True)
        return None
    except Exception as exc:
        traceback.print_exc()
        return scrub(f"{type(exc).__name__}: {exc}")


def refresh_button(label):
    return (f"<form method='post' action='/waivers/refresh'>"
            f"<button class='decline' style='width:100%;margin-bottom:12px'>"
            f"{e(label)}</button></form>")


def refresh_lineup(conn):
    """Work the verdicts out now. Returns an error to show, or None."""
    import lineup as ln

    username = os.environ.get("FANTASY_USER", "")
    if not username:
        return "FANTASY_USER is not set on the host, so I cannot look up " \
               "your leagues."
    try:
        got = ln.check(username, verbose=False)
        ln.store_check(conn, got)
        return None
    except ln.NoRankings:
        return "No ranking page could be read just now, so there was nothing " \
               "to compare against."
    except Exception as exc:
        traceback.print_exc()
        return scrub(f"{type(exc).__name__}: {exc}")


ATTENTION = ("RED", "YELLOW")


def headshot(row):
    """A player's face, or his team's badge for a defence.

    The image is a background on a coloured circle, so a player the CDN has
    no picture of shows the circle rather than a broken image, with no
    script needed to notice.
    """
    pid, pos = row["player_id"], (row["pos"] or "").upper()
    if pos == "DEF" and pid:
        url = f"https://sleepercdn.com/images/team_logos/nfl/{str(pid).lower()}.png"
    elif pid:
        url = f"https://sleepercdn.com/content/nfl/players/{pid}.jpg"
    else:
        return "<span class='face'></span>"
    return f"<span class='face' style=\"background-image:url('{e(url)}')\"></span>"


def where_and_points(row):
    bits = [row["matchup"]]
    if row["projection"] is not None:
        bits.append(f"{row['projection']:g} proj")
    return " &middot; ".join(e(b) for b in bits if b)


def player_line(row, emphasis=""):
    """One player: face, name, where he ranks, who he plays, what he may score."""
    under = " &middot; ".join(x for x in (e(row["rank_text"] or ""),
                                          where_and_points(row)) if x)
    return (f"<div class='player {emphasis}'>{headshot(row)}"
            f"<span class='who'><span class='pname'>{e(row['player_name'])}"
            f"</span><span class='under'>{under}</span></span>"
            f"<span class='slotname'>{e(row['slot'] or '')}</span></div>")


def alternatives_for(row, bench):
    """Bench players who could take this slot, best projection first."""
    allowed = SLOT_ELIGIBILITY.get(row["slot"]) or set()
    return [b for b in bench if (b["pos"] or "") in allowed]


def decision_card(row, bench):
    named = row["better_name"]
    options = alternatives_for(row, bench)
    # The player consensus actually prefers goes first; he is the reason
    # this card exists, and burying him under a projection ordering would
    # make the card disagree with its own headline.
    options.sort(key=lambda b: (named or "") not in (b["player_name"] or ""))

    out = [f"<div class='decide {e(row['verdict'])}'>",
           f"<div class='decidehead'><span>{e(row['league_name'] or '')}</span>"
           f"<span class='verdict {e(row['verdict'])}'>"
           f"{e(VERDICT_NOTE.get(row['verdict'], ''))}</span></div>",
           player_line(row, "starting")]
    if row["detail"]:
        out.append(f"<p class='advice'>{e(row['detail'])}</p>")
    if options:
        out.append("<div class='alts'><span class='altlabel'>On your bench"
                   "</span>")
        out.extend(player_line(b) for b in options[:4])
        out.append("</div>")
    out.append("</div>")
    return "".join(out)


def league_fold(name, started, bench, flagged):
    """A league's full lineup, folded shut when there is nothing to decide."""
    summary = (f"{len(started)} starters, {len(bench)} on the bench"
               if not flagged else
               f"{flagged} to decide &middot; {len(started)} starters")
    return ("<details class='fold'" + (" open" if flagged else "") + ">"
            f"<summary><span>{e(name)}</span>"
            f"<span class='meta'>{summary}</span></summary>"
            + "".join(player_line(r) for r in started)
            + ("<div class='altlabel'>Bench</div>" if bench else "")
            + "".join(player_line(r) for r in bench)
            + "</details>")


def render_lineup(conn, force=False):
    check = st.latest_lineup_check(conn)
    age = age_of(check)
    problem = None
    if force or not check or age is None or age > LINEUP_MAX_AGE:
        problem = refresh_lineup(conn)
        check = st.latest_lineup_check(conn) or check
        age = age_of(check)

    if not check:
        return page(nav("/lineup") + "<h1>Start / sit</h1>"
                    f"<p class='empty'>{e(problem or 'Nothing to show yet.')}"
                    "</p>", "Start / sit")

    rows = st.lineup_flags(conn, check["id"])
    started = [r for r in rows if (r["role"] or "starter") == "starter"]
    benched = [r for r in rows if (r["role"] or "starter") == "bench"]
    # Red before yellow, then by league: the order you would work through
    # them, not the order the database happened to return.
    decisions = sorted((r for r in started if r["verdict"] in ATTENTION),
                       key=lambda r: (ATTENTION.index(r["verdict"]),
                                      r["league_name"] or "", r["position"]))
    unknown = sum(1 for r in started if r["verdict"] == "UNKNOWN")

    bench_by_league = {}
    for r in benched:
        bench_by_league.setdefault(r["league_id"], []).append(r)

    headline = (f"<b>{len(decisions)}</b> to decide" if decisions
                else "<b>Nothing to change</b>")
    if unknown:
        headline += f" &middot; <b>{unknown}</b> unranked"

    out = [nav("/lineup"), "<h1>Start / sit</h1>",
           f"<div class='sub'>Week {e(check['week'])} &middot; checked "
           f"{e(said_ago(age))} &middot; against analyst rankings, not "
           f"projections</div>",
           f"<div class='counts'><span>{headline}</span></div>",
           "<form method='post' action='/lineup/refresh'>"
           "<button class='decline' style='width:100%;margin-bottom:12px'>"
           "Check again now</button></form>"]
    if problem:
        out.insert(3, f"<div class='bar'><span class='warn'>Could not "
                      f"refresh:</span> {e(problem)} Showing the last check."
                      "</div>")

    # The whole point of this page is the handful of slots worth thinking
    # about, and they were previously buried under whichever league happened
    # to sort first - which was the one with nothing wrong with it.
    if decisions:
        out.append("<h2><span>Worth a look</span></h2>")
        for row in decisions:
            out.append(decision_card(row, bench_by_league.get(row["league_id"], [])))

    out.append("<h2><span>Everything else</span></h2>")
    seen = []
    for r in started:
        if (r["league_id"], r["league_name"]) not in seen:
            seen.append((r["league_id"], r["league_name"]))
    for lid, lname in seen:
        mine = [r for r in started if r["league_id"] == lid]
        out.append(league_fold(lname or "", mine, bench_by_league.get(lid, []),
                               sum(1 for r in mine
                                   if r["verdict"] in ATTENTION)))
    return page("".join(out), "Start / sit")


def page(body, title):
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{e(title)}</title><style>{CSS}</style></head>"
            f"<body><div class='wrap'>{body}</div></body></html>").encode()


class Handler(BaseHTTPRequestHandler):
    db_path = None

    def _conn(self):
        return st.connect(self.db_path)

    def log_message(self, fmt, *args):
        pass  # keep the terminal readable

    # Every URL is rewritten to the one function that serves the app, so
    # what arrives here is the rewritten path, not the one the browser asked
    # for. vercel.json carries the original in __path; locally there is no
    # rewrite and self.path is already right.
    REWRITE_PREFIX = "/api/index"

    def route(self):
        parsed = urlparse(self.path)
        asked = (parse_qs(parsed.query).get("__path") or [""])[0]
        path = asked or parsed.path
        if path.startswith(self.REWRITE_PREFIX):
            path = path[len(self.REWRITE_PREFIX):]
        return path or "/"

    def _no_route(self, path):
        # Naming both paths turns "404" into the one fact that explains it:
        # whether the rewrite arrived, and what it arrived as.
        self.send_error(404, "No such page",
                        f"Asked for {path!r}; the function received "
                        f"{self.path!r}.")

    def _cookie(self, name):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return ""

    def _authed(self):
        return (not auth.auth_required()
                or auth.valid_session(self._cookie(auth.COOKIE)))

    def _send(self, code, body, ctype="text/html; charset=utf-8", headers=()):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _guard(self, handle):
        """Answer with the failure rather than dying inside the platform.

        An unhandled exception here reaches the browser as the host's own
        "this function crashed" page, which names nothing: the same page
        appears for a missing driver, an unreachable database and a bug.
        """
        try:
            handle()
        except Exception as exc:
            traceback.print_exc()
            detail = scrub(f"{type(exc).__name__}: {exc}")
            try:
                self._send(500, page(
                    "<h1>Something broke</h1>"
                    f"<p class='why'>{e(detail)}</p>"
                    "<p class='why'>The full traceback is in the host's "
                    "logs.</p>", "Error"))
            except Exception:
                pass

    def _json(self, code, obj):
        self._send(code, json.dumps(obj), "application/json")

    def do_GET(self):
        self._guard(self._get)

    def _get(self):
        path = self.route()
        if path == "/healthz":
            # Reaching the database is the half that breaks, and it breaks
            # after login, where the failure looks like a broken site.
            try:
                conn = self._conn()
                conn.execute("SELECT 1")
                conn.close()
                state = "ok"
            except Exception as exc:
                traceback.print_exc()
                # The type alone named three different faults across three
                # deploys, so the message has to be here. It names the
                # database host and the schema, so only someone who has
                # signed in or holds the API token gets to read it.
                state = f"database unreachable: {type(exc).__name__}"
                if self._authed() or auth.check_api_token(
                        self.headers.get("Authorization")):
                    state += f": {scrub(exc)}"
                else:
                    state += " (sign in or send the API token for the detail)"
            self._send(200, f"{state} (db={db.backend()})", "text/plain")
            return
        if path == "/login":
            self._send(200, page(LOGIN_HTML % "Sign in to review this week's"
                                 " waiver proposals.", "Sign in"))
            return
        if path == "/api/claims":
            if not auth.check_api_token(self.headers.get("Authorization")):
                self._json(401, {"error": auth.token_complaint(
                    self.headers.get("Authorization"))})
                return
            want_all = "include=submitted" in (urlparse(self.path).query or "")
            conn = self._conn()
            try:
                self._json(200, {"claims": claims_json(conn, want_all)})
            finally:
                conn.close()
            return
        if path not in ("/", "/lineup"):
            self._no_route(path)
            return
        if not self._authed():
            self.send_response(303)
            self.send_header("Location", "/login")
            self.end_headers()
            return
        conn = self._conn()
        try:
            body = render_lineup(conn) if path == "/lineup" else render(conn)
        finally:
            conn.close()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self._guard(self._post)

    def _post(self):
        path = self.route()
        length = int(self.headers.get("Content-Length") or 0)

        if path == "/login":
            form = parse_qs(self.rfile.read(length).decode("utf-8"))
            if auth.check_password((form.get("password") or [""])[0]):
                cookie = (f"{auth.COOKIE}={auth.make_session()}; Path=/; "
                          "HttpOnly; SameSite=Lax; Max-Age="
                          f"{auth.SESSION_DAYS * 86400}")
                if self.headers.get("X-Forwarded-Proto") == "https":
                    cookie += "; Secure"
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", cookie)
                self.end_headers()
            else:
                self._send(401, page(LOGIN_HTML % "That password was not "
                                     "right. Try again.", "Sign in"))
            return

        if path == "/api/proposals":
            if not auth.check_api_token(self.headers.get("Authorization")):
                self._json(401, {"error": auth.token_complaint(
                    self.headers.get("Authorization"))})
                return
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json(400, {"error": "body was not JSON"})
                return
            rows = payload.get("proposals") or []
            conn = self._conn()
            try:
                if already_ran_this_week(conn) and not payload.get("force"):
                    self._json(200, {"ok": True,
                                     "skipped": "already ran this week"})
                    return
                run_id = st.start_run(conn, payload.get("season"),
                                      payload.get("week"),
                                      payload.get("sources") or [])
                written = sum(1 for r in rows
                              if st.add_proposal(conn, run_id, **r) is not None)
                self._json(200, {"ok": True, "run": run_id,
                                 "written": written, "received": len(rows)})
            except Exception as exc:
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            finally:
                conn.close()
            return

        if path == "/api/lineup":
            if not auth.check_api_token(self.headers.get("Authorization")):
                self._json(401, {"error": auth.token_complaint(
                    self.headers.get("Authorization"))})
                return
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json(400, {"error": "body was not JSON"})
                return
            rows = payload.get("flags") or []
            conn = self._conn()
            try:
                # A lineup check replaces the last one rather than adding to
                # it: yesterday's advice about a lineup you have since changed
                # is worse than no advice, so only the newest check is shown.
                check_id = st.start_lineup_check(
                    conn, payload.get("season"), payload.get("week"),
                    payload.get("sources") or [])
                for row in rows:
                    st.add_lineup_flag(conn, check_id, **row)
                self._json(200, {"ok": True, "check": check_id,
                                 "written": len(rows)})
            except Exception as exc:
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            finally:
                conn.close()
            return

        if path == "/cron/weekly":
            if not auth.check_api_token(self.headers.get("Authorization")):
                self._json(401, {"error": auth.token_complaint(
                    self.headers.get("Authorization"))})
                return
            self.rfile.read(length)
            conn = self._conn()
            try:
                already = already_ran_this_week(conn)
            finally:
                conn.close()
            if already:
                # Firing twice must not double-propose: the idempotency key
                # includes the run, so a second run would look entirely new.
                self._json(200, {"ok": True, "skipped": "already ran this week"})
                return
            username = os.environ.get("FANTASY_USER", "")
            if not username:
                self._json(400, {"error": "FANTASY_USER is not set"})
                return
            import run_weekly
            try:
                run_weekly.main_for(username, self.db_path)
                self._json(200, {"ok": True})
            except Exception as exc:
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            return

        if path.startswith("/api/claims/"):
            if not auth.check_api_token(self.headers.get("Authorization")):
                self._json(401, {"error": auth.token_complaint(
                    self.headers.get("Authorization"))})
                return
            try:
                pid = int(path.rsplit("/", 2)[-2])
            except (ValueError, IndexError):
                self._json(400, {"error": "bad claim id"})
                return
            body = json.loads(self.rfile.read(length) or b"{}")
            conn = self._conn()
            try:
                if body.get("submitted"):
                    st.mark_submitted(conn, pid, bool(body.get("ok")),
                                      str(body.get("detail", ""))[:500])
                else:
                    # Nothing reached the league, so keep it retryable.
                    st.log(conn, "attempt_failed",
                           str(body.get("detail", ""))[:500], pid)
                    conn.commit()
                self._json(200, {"ok": True})
            finally:
                conn.close()
            return

        if path == "/waivers/refresh":
            if not self._authed():
                self.send_response(303)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            self.rfile.read(length)
            conn = self._conn()
            try:
                refresh_waivers(conn, self.db_path)
            finally:
                conn.close()
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
            return

        if path == "/lineup/refresh":
            if not self._authed():
                self.send_response(303)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            self.rfile.read(length)
            conn = self._conn()
            try:
                refresh_lineup(conn)
            finally:
                conn.close()
            self.send_response(303)
            self.send_header("Location", "/lineup")
            self.end_headers()
            return

        if path != "/decide":
            self._no_route(path)
            return
        if not self._authed():
            self.send_response(303)
            self.send_header("Location", "/login")
            self.end_headers()
            return
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        pid = (form.get("id") or [None])[0]
        action = (form.get("action") or [""])[0]
        bid = (form.get("bid") or [None])[0]

        conn = self._conn()
        try:
            if pid and action:
                status = {"approve": st.APPROVED, "decline": st.DECLINED,
                          "reopen": st.PENDING}.get(action)
                if status:
                    try:
                        bid_val = int(bid) if bid not in (None, "") else None
                    except ValueError:
                        bid_val = None
                    st.decide(conn, int(pid), status, bid=bid_val)
        finally:
            conn.close()
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()


def already_ran_this_week(conn):
    """Has the weekly job already produced a run in the last few days?"""
    run = st.latest_run(conn)
    if not run or not run["created_at"]:
        return False
    try:
        when = datetime.fromisoformat(run["created_at"])
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when) < timedelta(days=3)


def _scheduler_loop(username, when, db_path):
    """Run the weekly job on the host, at a fixed local time each week.

    The job has to run where the database lives. Running it on the Mac would
    file proposals into a file the hosted page cannot see, so on a deployment
    the host runs it itself and the Mac only ever submits.
    """
    import run_weekly

    day_names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    try:
        day_txt, _, hhmm = when.strip().lower().partition(" ")
        want_day = day_names.index(day_txt[:3])
        want_hour, _, want_min = hhmm.partition(":")
        want_hour, want_min = int(want_hour), int(want_min or 0)
    except (ValueError, IndexError):
        print(f"[scheduler] cannot read FANTASY_SCHEDULE={when!r}; expected "
              "something like 'tue 08:30'")
        return

    print(f"[scheduler] weekly job for {username} at {when}")
    last_run = None
    while True:
        now = time.localtime()
        stamp = (now.tm_year, now.tm_yday)
        if (now.tm_wday == want_day and now.tm_hour == want_hour
                and now.tm_min >= want_min and stamp != last_run):
            last_run = stamp
            print(f"[scheduler] running the weekly job ({time.asctime()})")
            try:
                run_weekly.main_for(username, db_path)
            except Exception as exc:
                print(f"[scheduler] job failed: {type(exc).__name__}: {exc}")
        time.sleep(30)


def start_scheduler(db_path):
    username = os.environ.get("FANTASY_USER", "")
    when = os.environ.get("FANTASY_SCHEDULE", "")
    if not (username and when):
        return
    threading.Thread(target=_scheduler_loop,
                     args=(username, when, db_path), daemon=True).start()


def main():
    localenv.load()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--db", default=str(st.DB_PATH))
    args = ap.parse_args()

    Handler.db_path = args.db
    start_scheduler(args.db)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    shown = "127.0.0.1" if args.host in ("127.0.0.1", "0.0.0.0") else args.host
    print(f"Approval page: http://{shown}:{args.port}")
    if args.host == "0.0.0.0":
        print("Bound to all interfaces — reachable from your phone over "
              "Tailscale/LAN.")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
