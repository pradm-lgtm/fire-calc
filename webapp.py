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

import claim_order
import cloud_auth as auth
import db
import expert_extract as ex
import localenv
import scores
import sleeper_client as sc
import store as st
import trades
from lineup import SLOT_ELIGIBILITY

CSS = """
/* Colour carries one meaning each. Blue is the only thing you can press,
   amber and red are the only things that need attention, and everything
   structural is grey. Two alarms for one fact - a red rule and a red label
   saying the same thing - is one alarm too many, so urgency lives in the
   rule and the words, never in both at once. */
:root { color-scheme: light dark;
        --bg:#f6f7f9; --card:#fff; --raise:#fff; --ink:#11141a;
        --muted:#646d7c; --line:#e4e7ec; --line-2:#d3d8e0;
        --action:#1d4ed8; --action-ink:#fff;
        --urgent:#c2321b; --warn:#8a5a00;
        --ok:#0a7d28; --ok-ink:#fff; --no:#b3261e;
        --qb:#7c3aed; --rb:#0a7d28; --wr:#1a56db; --te:#c2410c; --def:#475569; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0a0c10; --card:#13161c; --raise:#181c24; --ink:#eef1f6;
          --muted:#8d96a6; --line:#222731; --line-2:#2c323d;
          --action:#5b8cff; --action-ink:#0a0c10;
          --urgent:#ff6b5e; --warn:#e3a33a;
          --ok:#3ddc84; --ok-ink:#06210f; --no:#ff6b5e;
          --qb:#a78bfa; --rb:#4ade80; --wr:#7aa2f7; --te:#fb923c; --def:#94a3b8; }
}
* { box-sizing:border-box; -webkit-text-size-adjust:100%; }
body { margin:0; padding:18px 14px 40px; background:var(--bg); color:var(--ink);
       font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
.wrap { max-width:680px; margin:0 auto; }

/* Typography does the structuring. No tracked-out capitals: they read as
   template chrome rather than as information. */
h1 { font-size:26px; line-height:1.15; margin:0 0 2px; letter-spacing:-.02em; }
h2 { font-size:13px; font-weight:650; color:var(--muted); margin:28px 0 8px;
     display:flex; justify-content:space-between; align-items:baseline; gap:8px; }
.sub { color:var(--muted); font-size:13px; margin:0 0 16px; }
.meta { color:var(--muted); font-size:12px; font-weight:400; }
.foot { color:var(--muted); font-size:12px; margin:28px 0 0;
        border-top:1px solid var(--line); padding-top:12px; }

.brand { display:flex; align-items:center; gap:8px; font-weight:750;
         font-size:17px; letter-spacing:-.02em; margin-bottom:14px; }
.mark { width:24px; height:24px; color:var(--action); flex:none; }
nav { display:flex; gap:4px; margin-bottom:22px; padding:3px;
      background:var(--card); border:1px solid var(--line); border-radius:10px; }
nav a { flex:1; text-align:center; padding:8px; border-radius:7px;
        font-size:14px; font-weight:600; text-decoration:none;
        color:var(--muted); }
nav a.on { background:var(--action); color:var(--action-ink); }

/* One card shape, three weights of it. An urgent call is bigger, brighter
   and higher on the page than a marginal one. */
.call { background:var(--card); border:1px solid var(--line);
        border-radius:14px; padding:16px; margin-bottom:12px;
        border-left:3px solid var(--line-2); }
.call.urgent { background:var(--raise); border-left-color:var(--urgent);
               padding:20px; }
.call.close { border-left-color:var(--warn); }
.call.shut { border-left-color:var(--line-2); background:var(--card);
             padding:14px; opacity:.72; }
.call.shut h3 { font-size:16px; font-weight:600; }
.call h3 { margin:0; font-size:17px; line-height:1.25; letter-spacing:-.01em; }
.call.urgent h3 { font-size:21px; }
.call .why { color:var(--muted); font-size:14px; margin:6px 0 0; }
.calltop { display:flex; justify-content:space-between; gap:10px;
           align-items:baseline; margin-bottom:12px; }
.where { color:var(--muted); font-size:12px; white-space:nowrap; }

.player { display:flex; align-items:center; gap:11px; padding:9px 0; }
.player + .player { border-top:1px solid var(--line); }
.face { width:36px; height:36px; border-radius:50%; flex:none;
        background:var(--line) center/cover no-repeat; }
.player .who { display:flex; flex-direction:column; min-width:0; flex:1; }
.pname { font-weight:600; line-height:1.3; }
.under { color:var(--muted); font-size:12.5px; }
.slotname { color:var(--muted); font-size:12px; flex:none; }

/* The recommendation is the point of the card, so it is the one row that
   looks different from the rest. */
.player.pick { background:var(--bg); border-radius:10px; padding:9px 11px;
               margin:2px -3px; border-top:0; }
.player.pick .pname { font-size:17px; font-weight:700; }
.player.pick .face { width:44px; height:44px; }
.player.bench-out .pname { color:var(--muted); }
.player.bench-out .face { filter:grayscale(1); opacity:.65; }
.tick { font-size:12px; font-weight:700; color:var(--ok); flex:none; }
.rowlabel { font-size:12.5px; color:var(--muted); margin:14px 0 0; }
.others { margin-top:4px; }
.others > summary { font-size:13px; color:var(--muted); cursor:pointer;
                    padding:8px 0; list-style:none; }
.others > summary::-webkit-details-marker { display:none; }

.callfoot { display:flex; justify-content:space-between; align-items:center;
            gap:10px; margin-top:14px; padding-top:12px;
            border-top:1px solid var(--line); }
.done { font-size:12.5px; color:var(--muted); }

button { min-height:42px; padding:9px 16px; border-radius:9px;
         border:1px solid transparent; font-weight:600; cursor:pointer;
         font-size:15px; font-family:inherit; touch-action:manipulation; }
.primary { background:var(--action); color:var(--action-ink); }
.ghost { background:transparent; color:var(--ink); border-color:var(--line-2); }
.link { background:none; border:0; color:var(--muted); font-size:13px;
        padding:4px 0; min-height:0; text-decoration:underline; }
.approve { background:var(--ok); color:var(--ok-ink); flex:1; }
.decline { background:transparent; color:var(--no); border-color:var(--line);
           flex:1; }
form.row { display:flex; gap:8px; align-items:center; margin-top:14px;
           flex-wrap:wrap; }
.bidwrap { display:flex; align-items:center; gap:6px; }
input[type=number] { width:84px; min-height:44px; padding:10px; font-size:16px;
                     border:1px solid var(--line-2); border-radius:9px;
                     background:var(--bg); color:var(--ink); }
label { font-size:13px; color:var(--muted); }

.fold { background:var(--card); border:1px solid var(--line);
        border-radius:12px; padding:2px 16px 10px; margin-bottom:10px; }
.fold > summary { display:flex; justify-content:space-between; gap:8px;
                  align-items:baseline; padding:12px 0; cursor:pointer;
                  font-weight:600; list-style:none; }
.fold > summary::-webkit-details-marker { display:none; }
.fold > summary::after { content:'+'; color:var(--muted); font-weight:400; }
.fold[open] > summary::after { content:'−'; }

/* The waiver card. Hierarchy comes from size, weight and colour: nothing
   here is set in tracked-out capitals, which read as template chrome rather
   than as information. */
.card { background:var(--card); border:1px solid var(--line);
        border-radius:14px; padding:16px; margin-bottom:12px; }
.headline { display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; }
.dropline { display:flex; align-items:baseline; gap:8px; flex-wrap:wrap;
            margin-top:7px; }
.label { color:var(--muted); font-size:12.5px; width:38px; flex:none; }
.name { font-size:20px; font-weight:700; letter-spacing:-.01em; }
.name.small { font-size:15px; font-weight:600; color:var(--ink); }
.pos { font-size:11px; font-weight:700; padding:2px 6px; border-radius:5px;
       background:var(--line); }
.pos.QB{color:var(--qb)} .pos.RB{color:var(--rb)}
.pos.WR{color:var(--wr)} .pos.TE{color:var(--te)}
.pos.DEF,.pos.K{color:var(--def)}
.reason { font-size:14px; margin:12px 0 0; }
.why { color:var(--muted); font-size:12.5px; margin:8px 0 0; }
.quote { border-left:2px solid var(--line-2); padding-left:10px;
         margin:12px 0 0; color:var(--ink); font-size:13.5px;
         font-style:italic; }
.quote.muted { border-left-style:dashed; font-style:normal;
               color:var(--muted); font-size:13px; }

/* The bid and the button that spends it stay in one block, and the button
   says the number that is in the box. */
.bidrow { display:flex; align-items:center; gap:12px; flex-wrap:wrap;
          margin-top:16px; }
.bidwrap { display:flex; align-items:center; gap:7px; }
.bidwrap > span:first-child { font-size:13px; color:var(--muted); }
.of { font-size:13px; color:var(--muted); }
.guide { color:var(--muted); font-size:12.5px; margin-left:auto; }
.acts { display:flex; gap:8px; margin-top:10px; }

/* Who goes, stated plainly and changed in place. A native select was
   drawing over the bid box beneath it. */
.picker { margin-top:7px; }
.picker > summary { display:flex; align-items:baseline; gap:8px;
                    flex-wrap:wrap; cursor:pointer; list-style:none;
                    padding:2px 0; }
.picker > summary::-webkit-details-marker { display:none; }
.change { color:var(--action); font-size:13px; margin-left:auto; }
.picker[open] .change::after { content:' −'; }
.opts { border:1px solid var(--line); border-radius:10px; margin-top:8px;
        overflow:hidden; }
.opt { display:flex; gap:10px; padding:10px 12px; cursor:pointer;
       align-items:flex-start; }
.opt + .opt { border-top:1px solid var(--line); }
.opt input { margin-top:3px; flex:none; accent-color:var(--action); }
.optbody { display:flex; flex-direction:column; gap:2px; min-width:0; }
.optname { display:flex; align-items:center; gap:7px; font-size:14.5px;
           font-weight:600; flex-wrap:wrap; }
.optwhy { color:var(--muted); font-size:12.5px; }
.rec { font-size:11px; font-weight:700; color:var(--ok);
       border:1px solid var(--ok); border-radius:5px; padding:1px 5px; }

/* A chain of claims. Nothing else on a card carries an order, because
   nothing else has one. */
.chain { display:flex; align-items:center; gap:10px; flex-wrap:wrap;
         margin:-4px 0 12px; padding:8px 11px; border-radius:9px;
         background:var(--bg); border-left:3px solid var(--line-2); }
.chain.fallback { border-left-color:var(--action); }
.chain.first { border-left-color:var(--ok); }
.chaintext { margin:0; font-size:12.5px; color:var(--muted); flex:1;
             min-width:190px; }
.chaintext b { color:var(--ink); font-weight:650; }
.chain form { display:flex; gap:6px; }
.movebtn { min-height:32px; padding:5px 10px; font-size:12.5px;
           font-weight:600; border:1px solid var(--line-2); border-radius:8px;
           background:var(--card); color:var(--ink); font-family:inherit;
           cursor:pointer; }

.panel { border-color:var(--action); }
.paneltop { margin:0; font-size:16px; font-weight:650; }
.wide { width:100%; margin-top:12px; }
.queue { list-style:none; margin:10px 0 0; padding:0; color:var(--muted);
         font-size:13px; }
.queue li { margin:3px 0; }
.queueleague { color:var(--ink); font-weight:600; margin-top:8px; }
.match { background:var(--card); border:1px solid var(--line);
         border-radius:14px; padding:16px; margin-bottom:12px; }
.score { display:flex; justify-content:space-between; align-items:baseline;
         gap:10px; padding:10px 0; }
.score + .score { border-top:1px solid var(--line); }
.team { color:var(--muted); min-width:0; overflow:hidden;
        text-overflow:ellipsis; white-space:nowrap; }
.score.up .team { color:var(--ink); font-weight:650; }
.pts { font-size:26px; font-weight:700; font-variant-numeric:tabular-nums;
       letter-spacing:-.02em; flex:none; color:var(--muted); }
.score.up .pts { color:var(--ink); }
.pts.small { font-size:15px; font-weight:600; color:var(--ink); }
.projline { display:flex; justify-content:space-between; gap:10px;
            color:var(--muted); font-size:12px; margin:-6px 0 2px; }
.pair { display:grid; grid-template-columns:1fr auto 1fr; gap:6px;
        align-items:center; padding:7px 0; }
.pair + .pair { border-top:1px solid var(--line); }
.half { display:flex; align-items:center; gap:8px; min-width:0; }
.half.them { flex-direction:row-reverse; text-align:right; }
.half.pending { opacity:.6; }
.half .who { display:flex; flex-direction:column; min-width:0; flex:1; }
.half .pname { font-size:14px; font-weight:600; white-space:nowrap;
               overflow:hidden; text-overflow:ellipsis; }
.half .face { width:28px; height:28px; }
.slotchip { color:var(--muted); font-size:11px; font-weight:700; width:42px;
            text-align:center; flex:none; }
.swap { display:flex; gap:10px; padding:7px 0; align-items:baseline; }
.swap + .swap { border-top:1px solid var(--line); }
.swaplabel { color:var(--muted); font-size:12.5px; width:74px;
             flex:none; }
.changes { margin:4px 0 0; padding-left:18px; color:var(--muted);
           font-size:13px; }
.changes li { margin:2px 0; }
.settled { background:transparent; }
.settled.approved { border-color:var(--ok); }
.settled .name { font-size:17px; }
.settled .quote, .settled .reason { display:none; }
.state { display:flex; align-items:baseline; gap:8px; flex-wrap:wrap;
         font-size:15px; font-weight:650; margin:12px 0 0; }
.statemark { font-weight:700; }
.statenote { color:var(--muted); font-size:12.5px; font-weight:400; }
.state.approved { color:var(--ok); } .state.declined { color:var(--no); }
.state.submitted { color:var(--action); }
.state.failed { color:var(--urgent); }
.state.unconfirmed { color:var(--warn); }
.state.won { color:var(--ok); } .state.lost { color:var(--muted); }
.trouble { border-color:var(--urgent); }
.quiet { border-style:dashed; }
.trouble code { font-size:12.5px; background:var(--bg); padding:2px 5px;
                border-radius:5px; }
.outcome { font-size:13.5px; color:var(--ink); margin:10px 0 0; }
.empty { color:var(--muted); padding:20px 0; }
.bar { display:flex; flex-wrap:wrap; gap:4px 14px;
       background:var(--card); border:1px solid var(--line); border-radius:10px;
       padding:10px 13px; font-size:13px; color:var(--muted); margin-bottom:10px; }
.bar strong { color:var(--ink); }
.warn { color:var(--urgent); font-weight:600; }
.lname { color:var(--ink); font-size:16px; font-weight:700;
         letter-spacing:-.01em; }
.lnote { color:var(--muted); font-size:12.5px; margin:-6px 0 8px; }
.counts { display:flex; gap:14px; font-size:13px; color:var(--muted);
          margin-bottom:16px; flex-wrap:wrap; }
.counts b { color:var(--ink); }
.locked { font-size:12px; color:var(--muted); margin-left:8px; }

.progress { position:fixed; inset:0 auto auto 0; height:2px; width:0;
            background:var(--action); z-index:9; transition:width .35s ease; }
body.busy .progress { width:82%; transition:width 14s cubic-bezier(0,.8,.2,1); }
body.busy { cursor:progress; }
body.busy nav a, body.busy .call, body.busy .fold { opacity:.5; }
button[disabled] { opacity:.6; cursor:progress; }

@media (max-width:520px) {
  body { padding:14px 12px 32px; }
  h1 { font-size:23px; }
  .call.urgent h3 { font-size:19px; }
  .call, .call.urgent { padding:14px; }
  .bidwrap { width:100%; }
  input[type=number] { flex:1; width:auto; }
  .approve, .decline { flex:1 1 45%; }
  /* The analyst range drops under the bid box rather than being squeezed
     beside it, where it wraps to two words a line. */
  .guide { margin-left:0; width:100%; }
  .chaintext { min-width:0; }
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
  %s
  <h1>Spike</h1>
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
        "rank": r["rank"], "priority": r["priority"],
        "status": r["status"],
    } for r in rows]


def render(conn):
    run = st.latest_run(conn)
    if not run:
        return page(nav("/") + "<h1>Waiver proposals</h1>"
                    + refresh_button("Re-check waivers")
                    + "<p class='empty'>Nothing yet. The weekly job runs "
                      "Monday morning, and again after Monday night "
                      "football. Re-check waivers works it out now.</p>",
                    "Spike — waivers")

    rows = st.proposals_for_run(conn, run["id"])
    if not rows:
        return page(nav("/") + "<h1>Waiver proposals</h1>"
                    + refresh_button("Re-check waivers")
                    + (quiet_leagues(run)
                       or "<p class='empty'>That run produced no proposals."
                          "</p>"),
                    "Spike — waivers")

    by_league = {}
    for r in rows:
        by_league.setdefault((r["league_id"], r["league_name"]), []).append(r)

    # One counting system. The number in the header is the sum of the numbers
    # in the league headings, and nothing else on the page counts anything.
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
           refresh_button("Re-check waivers")]

    out.append(stale_warning(run))
    out.append(quiet_leagues(run))
    trouble = refresh_trouble(conn)
    if trouble:
        out.append(trouble)

    ready = [r for r in rows if r["status"] == st.APPROVED
             and not r["submitted_at"]]
    if ready:
        out.append(submit_panel(conn, ready))

    for (lid, lname), items in by_league.items():
        live = [i for i in items
                if i["status"] in (st.PENDING, st.APPROVED, st.SUBMITTED)]
        waiting = [i for i in items if i["status"] == st.PENDING]
        out.append(league_heading(conn, lid, lname, items, waiting, live))
        marks = chain_marks(live)
        for r in items:
            out.append(card(r, marks.get(r["id"])))
    return page("".join(out), "Spike — waivers")


def run_summary(conn):
    """What the page is actually showing, in numbers.

    "It looks stale" and "it is stale" were impossible to tell apart from a
    screenshot, and guessing at it twice was twice too often. This says which
    run is on the page, how old it is, and how many players each proposal
    offers to drop - which is the tell, since three of them means the run
    predates the change that offers the whole roster.
    """
    run = st.latest_run(conn)
    if not run:
        return {"run": None, "proposals": 0,
                "note": "no run has ever been filed here"}
    rows = st.proposals_for_run(conn, run["id"])
    counts = sorted(len(json.loads(r["drop_options"] or "[]")) for r in rows)
    failed = st.last_event(conn, "refresh_failed")
    ok = st.last_event(conn, "refresh_ok")
    return {
        "run": run["id"],
        "season": run["season"],
        "week": run["week"],
        "filed": run["created_at"],
        "age": said_ago(age_of(run)),
        "proposals": len(rows),
        "drop_options": {"fewest": counts[0] if counts else 0,
                         "most": counts[-1] if counts else 0},
        "statuses": {k: sum(1 for r in rows if r["status"] == k)
                     for k in {r["status"] for r in rows}},
        "last_refresh_failure": (
            None if not failed or (ok and ok["id"] > failed["id"])
            else {"at": failed["at"], "detail": failed["detail"]}),
        "database": conn.kind,
    }


def quiet_leagues(run):
    """Name the leagues that produced nothing, and why.

    A league with a settled roster and a league whose reasoning fell over
    both showed up as an absence, and an absence is not something anyone
    notices. Saying "OTG: nothing, because the best player available is not
    worth more than your weakest droppable one" is a conclusion; a missing
    heading is not.
    """
    try:
        quiet = json.loads(run["note"] or "{}")
    except (ValueError, TypeError):
        return ""
    if not isinstance(quiet, dict) or not quiet:
        return ""
    rows = "".join(f"<li><b>{e(name)}</b> &mdash; {e(why)}</li>"
                   for name, why in sorted(quiet.items()))
    return (f"<div class='card quiet'><p class='paneltop'>Nothing to do in "
            f"{len(quiet)} league{'s' if len(quiet) != 1 else ''}</p>"
            f"<ul class='queue'>{rows}</ul></div>")


def stale_warning(run):
    """Say when what is on the page is too old to act on.

    Waivers are a weekly thing, so a run from the week before last is not
    merely out of date: the players in it have been claimed and the article
    it read has been replaced.
    """
    days = (age_of(run) or timedelta(0)).days
    if days < 4:
        return ""
    return (f"<div class='card trouble'><p class='paneltop'>These proposals "
            f"are {days} days old</p><p class='why'>They were worked out for "
            f"week {e(run['week'])}, from articles published then, against "
            "rosters as they were then. Re-check waivers below, or run "
            "<code>python3 run_weekly.py --force</code> where the weekly job "
            "lives if this page cannot reach the article sites.</p></div>")


def refresh_trouble(conn):
    """Say so when the last attempt to work the proposals out again failed.

    The button used to swallow the error and redirect, so a refresh that
    never ran was indistinguishable from one that found nothing to change -
    and what you were reading stayed weeks old without saying so.
    """
    failed = st.last_event(conn, "refresh_failed")
    if not failed:
        return ""
    ok = st.last_event(conn, "refresh_ok")
    if ok and ok["id"] > failed["id"]:
        return ""
    when = said_ago(age_of({"created_at": failed["at"]}))
    return (f"<div class='card trouble'><p class='paneltop'>The last re-check "
            f"failed, {e(when)}</p>"
            f"<p class='why'>{e(failed['detail'] or 'no detail recorded')}"
            "</p><p class='why'>What is below is from the run before it, so "
            "it may be out of date. Re-checking from a computer that can "
            "reach the article sites is the way round it.</p></div>")


def league_heading(conn, lid, lname, items, waiting, live):
    """The league, what it costs, and what its name does not tell you."""
    budget = items[0]["max_bid"] or 0
    committed = st.budget_committed(conn, lid)
    asked = sum(i["bid"] or 0 for i in waiting)
    note = claim_order.field(items[0], "league_note")

    bar = (f"<span><strong data-left>{budget - committed}</strong> of "
           f"{budget} left</span>")
    if waiting:
        # Live, because the number that matters is what is left after the
        # bids in the boxes below - not after the ones the run suggested.
        bar += (f"<span>approving all {len(waiting)} costs <strong data-cost>"
                f"{asked}</strong>, leaving <strong data-after>"
                f"{budget - committed - asked}</strong></span>")
    bar += "<span class='warn' data-over hidden>that is more than you have"\
           "</span>"

    fallbacks = claim_order.blockers(live)
    if fallbacks:
        one = len(fallbacks) == 1
        bar += ("<span>" + (
            "one of these is a fallback and only runs if the claim above it "
            "fails" if one else
            f"{len(fallbacks)} of these are fallbacks, each running only if "
            "the claim above it fails") + ", so the real cost is lower</span>")

    return (f"<h2><span class='lname'>{e(lname)}</span>"
            f"<span class='meta'>{len(waiting)} to review</span></h2>"
            + (f"<div class='lnote'>{e(note)}</div>" if note else "")
            + f"<div class='bar' data-league='{e(lid)}' "
              f"data-budget='{budget}' data-committed='{committed}'>{bar}</div>")


def chain_marks(live):
    """What to say on each card about the claims it competes with.

    Only claims that clash get anything at all. Bids decide who wins a player,
    not the order claims were filed in, so a claim competing with nothing is
    in no position and is not given arrows implying it is.
    """
    seq = claim_order.ordered(live)
    blocking = claim_order.blockers(seq)
    role = claim_order.roles(seq)
    places = {r["id"]: n for n, r in enumerate(seq)}
    marks = {}
    for r in seq:
        if r["id"] not in role:
            continue
        n = places[r["id"]]
        marks[r["id"]] = {
            "role": role[r["id"]],
            "blocker": blocking.get(r["id"]),
            "up": n > 0 and not r["submitted_at"],
            "down": n < len(seq) - 1 and not r["submitted_at"],
        }
    return marks


def submit_panel(conn, ready):
    """Ask the Mac to place what you have approved.

    The page runs on a server with no browser and no Sleeper session, so it
    cannot place anything itself. Pressing this records the request; the Mac
    that does have a browser picks it up within the minute.
    """
    last = st.last_submit_request(conn)
    note, trouble = "", False
    if last:
        if last["finished_at"]:
            # These claims are still approved and unplaced, and the attempt
            # to place them is over: whatever it says, it did not work. Said
            # plainly, because the alternative is pressing the button again
            # and watching nothing happen.
            when = e(said_ago(age_of_field(last, "finished_at")))
            note = (f"The attempt {when} did not place them: "
                    f"{e(last['detail'] or 'no reason given')}")
            trouble = True
        elif last["claimed_at"]:
            note = "Your Mac is placing them now."
        else:
            note = ("Waiting for your Mac to pick this up. It has to be awake, "
                    "with Chrome open and signed in to Sleeper.")
    waiting = last and not last["finished_at"]
    return (f"<div class='card panel{' trouble' if trouble else ''}'>"
            f"<p class='paneltop'>{len(ready)} claim"
            f"{'s' if len(ready) != 1 else ''} approved and not yet placed</p>"
            + running_order(ready)
            + (f"<p class='{'outcome' if trouble else 'why'}'>{note}</p>"
               if note else "")
            + ("" if waiting else
               "<form method='post' action='/submit'>"
               "<button class='primary wide'>"
               "Place them in Sleeper now</button></form>")
            + "</div>")


def running_order(ready):
    """The queue as it will be filed, so the order is visible before it is sent.

    Pressing the button is the last chance to notice that a fallback sits
    above the claim it was meant to back up, and the only place that is
    obvious is a list of all of them together.
    """
    lines = []
    for rows in claim_order.by_league(ready).values():
        seq = claim_order.ordered(rows)
        fallbacks = claim_order.blockers(seq)
        lines.append(f"<li class='queueleague'>{e(seq[0]['league_name'])}</li>")
        for n, r in enumerate(seq, start=1):
            bid = f" for {r['bid']}" if r["bid"] is not None else ""
            # Two claims for the same player differ only in who they cut, so
            # the drop is the half that tells them apart here.
            cut = (f", dropping {e(strip_paren(r['drop_player_name']))}"
                   if r["drop_player_name"] else "")
            tail = " &mdash; only if the one above fails" \
                if r["id"] in fallbacks else ""
            lines.append(
                f"<li>{n}. {e(strip_paren(r['add_player_name']))}{e(bid)}"
                f"{cut}{tail}</li>")
    return f"<ul class='queue'>{''.join(lines)}</ul>" if lines else ""


def age_of_field(row, field):
    return age_of({"created_at": row[field]})


def card(r, mark=None):
    """One claim, scannable in a couple of seconds.

    Top to bottom: what would happen, the one reason it is being suggested,
    what an analyst actually said about the man, and the two decisions. The
    drop is shown by name rather than hidden behind a list to open, because
    who goes is half of what you are approving.
    """
    pending = r["status"] == st.PENDING
    options = json.loads(r["drop_options"] or "[]")
    # A decided card stays where it was - the list should not jump under you
    # - but it stops looking like one waiting for an answer.
    settled = "" if pending else f" settled {e(r['status'])}"
    bits = [f"<article class='card{settled}'>"]
    if pending:
        # The form is declared once, empty, and the controls scattered down
        # the card join it by id. That is what lets the drop list sit above
        # the bid box in the flow instead of floating over it.
        bits.append(f"<form id='f{e(r['id'])}' method='post' action='/decide'>"
                    f"<input type='hidden' name='id' value='{e(r['id'])}'>"
                    "</form>")
    if mark:
        bits.append(chain_banner(r, mark))

    bits += ["<div class='headline'>",
             "<span class='label'>Add</span>",
             f"<span class='name'>{e(strip_paren(r['add_player_name']))}</span>",
             pos_chip(r["add_position"]),
             "</div>"]

    if pending and options:
        bits.append(drop_picker(r, options))
    elif r["drop_player_name"]:
        bits += ["<div class='dropline'>",
                 "<span class='label'>Drop</span>",
                 f"<span class='name small'>"
                 f"{e(strip_paren(r['drop_player_name']))}</span>",
                 pos_chip(r["drop_position"]),
                 "</div>"]

    bits.append(reason_line(r, options))

    bits.append(evidence(r))

    if pending:
        bits.append(decide_form(r))
    else:
        bits.append(outcome(r, options))
    bits.append("</article>")
    return "".join(bits)


def plain_why(text):
    """A drop's reason as text, whatever era it was written in.

    Reasons used to be built with markup entities in them. Those rows are
    still in the database and escaping one would put a literal &amp;middot;
    on the card, so the entity is turned back into the character it names.
    """
    return str(text or "").replace("&middot;", "\u00b7").replace(
        "&mdash;", "\u2014")


def reason_line(r, options):
    """Why this drop, tied to the man actually selected.

    It used to be written once, when the run chose a drop, and then stayed
    put while you changed your mind underneath it - so a card could argue for
    cutting Gainwell above a form set to cut Dobbins. It is built from the
    chosen option now, and the script rewrites it when you choose another.
    """
    chosen = str(r["drop_player_id"] or "")
    picked = next((o for o in options if str(o.get("id")) == chosen), None)
    if picked is None:
        return (f"<p class='reason'>{e(plain_why(r['rationale']))}</p>"
                if r["rationale"] else "")
    who = strip_paren(picked.get("name") or "")
    why = plain_why(picked.get("why"))
    return ("<p class='reason'>Dropping <b data-dropwho>" + e(who)
            + "</b><span data-dropwhy>" + (f" &mdash; {e(why)}" if why else "")
            + "</span>.</p>")


def chain_banner(r, mark):
    """Say what this claim competes with, and offer to change the order."""
    if mark["role"] == "first":
        text = ("<b>First choice.</b> A claim below falls back to this one "
                "if it fails.")
    else:
        above, why = mark["blocker"]
        who = strip_paren(above["add_player_name"])
        cost = f" at {above['bid']}" if above["bid"] is not None else ""
        text = (f"<b>Fallback.</b> Runs only if <b>{e(who)}{e(cost)}</b> "
                f"fails, because {e(why)}.")
    moves = ""
    if mark["up"] or mark["down"]:
        moves = ("<form method='post' action='/order'>"
                 f"<input type='hidden' name='id' value='{e(r['id'])}'>"
                 + ("<button class='movebtn' name='dir' value='up'>"
                    "Move up</button>" if mark["up"] else "")
                 + ("<button class='movebtn' name='dir' value='down'>"
                    "Move down</button>" if mark["down"] else "")
                 + "</form>")
    return (f"<div class='chain {e(mark['role'])}'>"
            f"<p class='chaintext'>{text}</p>{moves}</div>")


def evidence(r):
    """What an analyst said, when an analyst actually said something.

    The quote is re-checked here and not only where it was taken. A quote
    written before the extractor learned to reject ranking-page furniture is
    still in the database, and the first thing read on a card should not be a
    sentence naming eleven other men.
    """
    srcs = json.loads(r["sources"] or "[]")
    # One site that filed twice is one name here, to match the count beside
    # it - "3 analysts - rotoballer.com, rotoballer.com" was arguing with
    # itself in the same sentence.
    seen, names = set(), []
    for url in srcs:
        name = short_source(url)
        if name not in seen:
            seen.add(name)
            names.append(name)
    src_txt = ", ".join(names)
    named = e(f"Named by {r['consensus']} analyst"
              f"{'s' if (r['consensus'] or 0) != 1 else ''}")
    if src_txt:
        named += f" &middot; {e(src_txt)}"
    quote = r["quote"] or ""
    if ex.about(quote, r["add_player_name"]):
        body = f"<blockquote class='quote'>{e(quote)}</blockquote>"
    else:
        body = ("<p class='quote muted'>Limited recent coverage &mdash; he is "
                "on the lists, but nobody wrote him up.</p>")
    return body + f"<p class='why'>{named}</p>"


def decide_form(r):
    bid = r["bid"] if r["bid"] is not None else 0
    if r["bid_low"] is not None and r["bid_high"] is not None:
        span = (f"{r['bid_low']}" if r["bid_low"] == r["bid_high"]
                else f"{r['bid_low']} to {r['bid_high']}")
        guide = f"Analysts bid {span}"
    else:
        guide = "No analyst put a number on him"
    return ("<div class='bidrow'>"
            "<label class='bidwrap'><span>Bid</span>"
            f"<input type='number' name='bid' form='f{e(r['id'])}'"
            f" inputmode='numeric' min='0' max='{e(r['max_bid'] or 100)}'"
            f" data-bid data-league='{e(r['league_id'])}' value='{e(bid)}'>"
            f"<span class='of'>of {e(r['max_bid'] or 100)}</span></label>"
            f"<span class='guide'>{e(guide)}</span></div>"
            "<div class='acts'>"
            f"<button class='approve' form='f{e(r['id'])}' name='action'"
            f" value='approve' data-approve>Approve at "
            f"<span data-approve-bid>{e(bid)}</span></button>"
            f"<button class='decline' form='f{e(r['id'])}' name='action'"
            " value='decline'>Decline</button></div>")


# What a decided card says about itself, and the mark that goes with it.
STATES = {
    st.APPROVED: ("\u2713", "Approved", "waiting to be placed in Sleeper"),
    st.DECLINED: ("\u2715", "Declined", "this one will not be filed"),
    st.SUBMITTED: ("\u2713", "Placed in Sleeper",
                   "Sleeper shows nothing until waivers run"),
    st.FAILED: ("!", "Could not be placed", ""),
    st.UNCONFIRMED: ("?", "Placed, but not confirmed",
                     "check Sleeper before doing anything about it"),
    st.WON: ("\u2713", "Won him", "the claim went through"),
    st.LOST: ("\u2715", "Outbid", "somebody bid more; nothing was spent"),
    st.SKIPPED: ("\u2013", "No longer possible", "the league moved on"),
}


def outcome(r, options):
    """The result, stated plainly enough to see from across the room.

    A green line of small text under a card that still looked exactly like
    the ones above it was not enough to answer "did that work?".
    """
    glyph, label, note = STATES.get(
        r["status"], ("", str(r["status"]).title(), ""))
    extra = f" at {r['bid']}" if r["bid"] is not None else ""
    line = (f"<p class='state {e(r['status'])}'><span class='statemark'>{glyph}"
            f"</span>{e(label)}{e(extra)}"
            + (f"<span class='statenote'>{e(note)}</span>" if note else "")
            + "</p>")
    if r["result"]:
        line += f"<p class='why'>{e(r['result'])}</p>"
    if r["status"] in (st.APPROVED, st.DECLINED):
        line += ("<form method='post' action='/decide'>"
                 f"<input type='hidden' name='id' value='{e(r['id'])}'>"
                 "<button class='link' name='action' value='reopen'>"
                 "Undo</button></form>")
    if r["status"] == st.APPROVED:
        line += fallback_form(r, options)
    return line


def fallback_form(r, options):
    """Offer a second claim for the same player, cutting somebody else.

    Only where there is somebody else to cut: a copy with the same drop is a
    duplicate, and the league would process it as one.
    """
    spare = [o for o in options
             if str(o.get("id")) != str(r["drop_player_id"] or "")]
    if not spare:
        return ""
    return ("<form method='post' action='/fallback'>"
            f"<input type='hidden' name='id' value='{e(r['id'])}'>"
            "<button class='ghost wide'>"
            f"Also claim {e(strip_paren(r['add_player_name']))} for a "
            "different drop</button></form>")


def drop_pick(conn, proposal_id, drop_id):
    """The option a chosen drop id refers to, or None.

    Looked up rather than posted, so the form cannot name one player and
    identify another - and so the position comes along with the name instead
    of being left at whatever the run first suggested.
    """
    if not drop_id:
        return None
    row = conn.execute("SELECT drop_options FROM proposals WHERE id = ?",
                       (int(proposal_id),)).fetchone()
    for option in json.loads((row and row["drop_options"]) or "[]"):
        if str(option.get("id")) == str(drop_id):
            return option
    return None


def drop_name(conn, proposal_id, drop_id):
    """Just the name, for callers that only want that."""
    pick = drop_pick(conn, proposal_id, drop_id)
    return pick.get("name") if pick else None


def drop_picker(r, options):
    """Who goes - shown by name, changed in place.

    Closed, it reads as a plain statement of the recommendation, because
    whether anyone is worth dropping decides whether the add is worth
    bidding on at all and that should not need a tap to see. Open, it grows
    downward in the flow of the card. A native select was covering the bid
    box behind it, and a list you cannot see past is a bad place to make
    this particular choice.
    """
    chosen = str(r["drop_player_id"] or "")
    picked = next((o for o in options if str(o.get("id")) == chosen),
                  options[0] if options else None)
    rows = []
    for n, option in enumerate(options):
        pid = str(option.get("id") or "")
        name = strip_paren(option.get("name") or "")
        # The first option is what the run picked, whatever is selected now:
        # the label says where the suggestion came from, not what you chose.
        tag = "<span class='rec'>Recommended</span>" if n == 0 else ""
        rows.append(
            f"<label class='opt'>"
            f"<input type='radio' name='drop' form='f{e(r['id'])}'"
            f" value='{e(pid)}'{' checked' if pid == chosen else ''}"
            f" data-name=\"{e(name)}\" data-why=\"{e(plain_why(option.get('why')))}\""
            f" data-pos='{e(option.get('position') or '')}'>"
            f"<span class='optbody'><span class='optname'>{e(name)}"
            f"{pos_chip(option.get('position'))}{tag}</span>"
            f"<span class='optwhy'>{e(plain_why(option.get('why')))}</span></span>"
            "</label>")
    return ("<details class='picker'><summary>"
            "<span class='label'>Drop</span>"
            f"<span class='name small' data-dropname>"
            f"{e(strip_paren((picked or {}).get('name') or ''))}</span>"
            f"<span data-droppos>{pos_chip((picked or {}).get('position'))}</span>"
            "<span class='change'>Change</span></summary>"
            f"<div class='opts'>{''.join(rows)}</div></details>")


def pos_chip(pos):
    """Position badge. Colour is the fastest thing to scan on a phone."""
    p = (pos or "").upper()
    cls = p if p in ("QB", "RB", "WR", "TE", "DEF", "K") else ""
    return f"<span class='pos {cls}'>{e(p or '?')}</span>"


def strip_paren(name):
    """'Dylan Sampson (CLE RB)' -> 'Dylan Sampson' - the chip carries position.

    Shared with the claim ordering, which puts the same name in the sentence
    explaining why one claim is a fallback to another.
    """
    return claim_order.plain(name)


def short_source(url):
    try:
        host = urlparse(url).netloc or url
        return host.replace("www.", "")
    except Exception:
        return str(url)


# A football at the moment it is spiked: pointed down, bouncing away. Inline
# because one request that cannot fail beats an image that can.
LOGO = ("<svg class='mark' viewBox='0 0 32 32' aria-hidden='true'>"
        "<ellipse cx='16' cy='16' rx='7.5' ry='11' fill='currentColor'"
        " transform='rotate(28 16 16)'/>"
        "<path d='M10.6 21.4 21.4 10.6' stroke='var(--card)'"
        " stroke-width='1.6' stroke-linecap='round'/>"
        "<path d='M13.2 17.4h3.2M15.6 15h3.2' stroke='var(--card)'"
        " stroke-width='1.4' stroke-linecap='round'/>"
        "<path d='M25 7c1.6-1.2 3-1.4 4-0.6' stroke='currentColor'"
        " stroke-width='1.6' fill='none' stroke-linecap='round'"
        " opacity='.45'/></svg>")

FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'"
           " viewBox='0 0 32 32'%3E%3Cellipse cx='16' cy='16' rx='7.5'"
           " ry='11' fill='%231a56db' transform='rotate(28 16 16)'/%3E"
           "%3C/svg%3E")


def login_page(message):
    return LOGIN_HTML % (LOGO, e(message))


def brand():
    return f"<div class='brand'>{LOGO}<span>Spike</span></div>"


def nav(here):
    tabs = (("/scores", "Scores"), ("/", "Waivers"),
            ("/lineup", "Start / sit"), ("/trades", "Trades"))
    return brand() + "<nav>" + "".join(
        f"<a class='{'on' if path == here else ''}' href='{path}'>{label}</a>"
        for path, label in tabs) + "</nav>"


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
        st.log(conn, "refresh_ok", "")
        conn.commit()
        return None
    except Exception as exc:
        traceback.print_exc()
        # Recorded rather than returned into the void. A refresh that fails
        # silently looks exactly like one that found nothing new, which is
        # how a fortnight-old set of proposals can sit there looking current.
        detail = scrub(f"{type(exc).__name__}: {exc}")
        st.log(conn, "refresh_failed", detail)
        conn.commit()
        return detail


def refresh_button(label):
    return (f"<form method='post' action='/waivers/refresh'>"
            f"<button class='ghost' style='width:100%;margin-bottom:12px'>"
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

    A background image on a coloured circle, so a player the image service
    has never heard of shows the circle rather than a broken icon, with no
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


def rank_label(row, flex_scale):
    """Where he ranks, on the scale the slot in question is decided on.

    A positional rank cannot answer a flex question: WR12 against RB20 for
    one spot is not a comparison.
    """
    if flex_scale and row["overall_text"] and row["overall_text"] != "unranked":
        return row["overall_text"]
    return row["rank_text"] or ""


def where_and_points(row):
    bits = [row["matchup"]]
    if row["projection"] is not None:
        bits.append(f"{row['projection']:g} proj")
    return " &middot; ".join(e(b) for b in bits if b)


def short_name(label):
    return (label or "").split(" (")[0].strip()


def player_line(row, kind="", flex_scale=False, both=False, note="",
                quiet=False):
    """One player. `kind` is what this row is here to say, not how loud."""
    ranks = [rank_label(row, flex_scale)]
    if both and row["overall_text"] and row["overall_text"] not in ranks:
        ranks.append(row["overall_text"])
    under = " &middot; ".join(
        x for x in [e(r) for r in ranks if r] + [where_and_points(row)] if x)
    lock = ("<span class='locked'>playing now</span>"
            if row["locked"] and not quiet else "")
    tail = (f"<span class='tick'>{e(note)}</span>" if note
            else f"<span class='slotname'>{e(row['slot'] or '')}</span>")
    return (f"<div class='player {kind}'>{headshot(row)}"
            f"<span class='who'><span class='pname'>{e(row['player_name'])}"
            f"{lock}</span><span class='under'>{under}</span></span>"
            f"{tail}</div>")


def alternatives_for(row, bench):
    allowed = SLOT_ELIGIBILITY.get(row["slot"]) or set()
    return [b for b in bench if (b["pos"] or "") in allowed]


def settle_form(row, label, css="ghost"):
    return ("<form method='post' action='/lineup/done'>"
            f"<input type='hidden' name='league' value='{e(row['league_id'])}'>"
            f"<input type='hidden' name='slot' value='{e(row['slot'] or '')}'>"
            f"<input type='hidden' name='player' value='{e(row['player_id'] or '')}'>"
            f"<button class='{css}'>{e(label)}</button></form>")


def undo_form(row):
    return ("<form method='post' action='/lineup/undo'>"
            f"<input type='hidden' name='league' value='{e(row['league_id'])}'>"
            f"<input type='hidden' name='slot' value='{e(row['slot'] or '')}'>"
            f"<input type='hidden' name='player' value='{e(row['player_id'] or '')}'>"
            "<button class='link'>Undo</button></form>")


def call_card(row, bench, urgent):
    """One lineup call, led by what to do about it.

    The old card stated a colour and left you to work out the action from a
    sentence underneath. The action is the headline now, the reason is one
    line under it, and the player to start is the only row on the card that
    looks different from the others.
    """
    shut = bool(row["locked"])
    flex = len(SLOT_ELIGIBILITY.get(row["slot"], ())) > 1
    options = alternatives_for(row, bench)
    named = short_name(row["better_name"])
    options.sort(key=lambda b: named not in (b["player_name"] or ""))
    pick = options[0] if (named and options
                          and named in (options[0]["player_name"] or "")) else None

    # A closed call is a record, so it is never phrased as an instruction.
    # "Start Robinson over Flowers" above "Kickoff has passed" tells you to
    # do something and then that you cannot.
    mine = short_name(row["player_name"])
    if shut:
        headline = f"The call was {named} over {mine}" if pick else \
                   f"{mine} was flagged"
    else:
        headline = f"Start {named} over {mine}" if pick else \
                   f"Reconsider {mine}"

    classes = ("call " + ("urgent" if urgent else "close")
               + (" shut" if shut else ""))
    out = [f"<article class='{classes}'>",
           "<div class='calltop'>",
           f"<div><h3>{e(headline)}</h3>",
           (f"<p class='why'>{e(row['detail'])}</p>" if row["detail"] else ""),
           "</div>",
           f"<span class='where'>{e(row['league_name'] or '')} &middot; "
           f"{e(row['slot'] or '')}</span></div>"]

    if pick:
        # No "Start" on a call that has closed, and no "playing now" on
        # either player: the footer already says the game has gone.
        out.append(player_line(pick, "pick", flex_scale=flex,
                               note="" if shut else "Start", quiet=shut))
        out.append(f"<p class='rowlabel'>{'Rather than' if shut else 'Instead of'}</p>")
        out.append(player_line(row, "bench-out", flex_scale=flex, quiet=shut))
        rest = options[1:]
    else:
        out.append(player_line(row, "pick", flex_scale=flex, quiet=shut))
        rest = options

    if rest:
        out.append(f"<details class='others'><summary>{len(rest)} other "
                   f"option{'s' if len(rest) > 1 else ''} for this slot"
                   "</summary>")
        out.extend(player_line(b, flex_scale=flex) for b in rest[:4])
        out.append("</details>")

    if shut:
        # Kept as a record of what the call was, not as something to do.
        out.append("<div class='callfoot'><span class='done'>Kickoff has "
                   "passed. This is what the call was.</span></div></article>")
    else:
        out.append("<div class='callfoot'><span class='done'>Decided in your "
                   "league app?</span>" + settle_form(row, "Mark as done") +
                   "</div></article>")
    return "".join(out)


def league_fold(name, started, bench, outstanding):
    summary = (f"{outstanding} still open" if outstanding
               else f"{len(started)} starters, {len(bench)} benched")
    return ("<details class='fold'" + (" open" if outstanding else "") + ">"
            f"<summary><span>{e(name)}</span>"
            f"<span class='meta'>{summary}</span></summary>"
            + "".join(player_line(
                r, flex_scale=len(SLOT_ELIGIBILITY.get(r["slot"], ())) > 1)
                for r in started)
            + ("<p class='rowlabel'>Bench</p>" if bench else "")
            + "".join(player_line(r, both=True) for r in bench)
            + "</details>")


def plays_thursday(row):
    """Is this player's game the Thursday one, and still ahead of us?

    Locked rows are left alone: once the game has kicked off the urgency is
    gone and what is left is a record, which belongs with the other closed
    calls rather than under a heading about a deadline.
    """
    return claim_order.field(row, "day") == "Thu" and not row["locked"]


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
                    "</p>", "Spike \u2014 start / sit")

    rows = st.lineup_flags(conn, check["id"])
    handled = st.settled(conn, check["season"], check["week"])
    started = [r for r in rows if (r["role"] or "starter") == "starter"]
    benched = [r for r in rows if (r["role"] or "starter") == "bench"]

    bench_by_league = {}
    for r in benched:
        bench_by_league.setdefault(r["league_id"], []).append(r)

    def key(r):
        return (r["league_id"], r["slot"], r["player_id"])

    # A call stays on the page once its game starts. It is closed, not
    # resolved: hiding it would claim the lineup agrees with consensus when
    # what actually happened is that the chance to change it passed.
    open_calls = [r for r in started if r["verdict"] in ATTENTION
                  and key(r) not in handled]
    done = [r for r in started if r["verdict"] in ATTENTION
            and key(r) in handled]

    def tier(colour):
        return [r for r in open_calls
                if r["verdict"] == colour and not r["locked"]]

    urgent, close = tier("RED"), tier("YELLOW")
    # Kept apart rather than sorted to the bottom of a list headed "Fix
    # these", which tells you to act on something you cannot act on.
    closed = [r for r in open_calls if r["locked"]]

    # Thursday players are a different decision from everyone else. They come
    # out of both lists and into their own, because a deadline tonight and a
    # deadline on Sunday morning do not belong under one heading - and the
    # cost of missing the first one is the whole week for that player.
    thursday = [r for r in urgent + close if plays_thursday(r)]
    urgent = [r for r in urgent if not plays_thursday(r)]
    close = [r for r in close if not plays_thursday(r)]

    out = [nav("/lineup"), "<h1>Start / sit</h1>",
           f"<p class='sub'>Week {e(check['week'])} &middot; checked "
           f"{e(said_ago(age))}</p>"]
    if problem:
        out.append(f"<div class='bar'><span class='warn'>Could not "
                   f"refresh:</span> {e(problem)} Showing the last check."
                   "</div>")

    def section(title, rows, urgent, note=""):
        if not rows:
            return
        out.append(f"<h2><span>{title}</span>"
                   f"<span class='meta'>{e(note or len(rows))}</span></h2>")
        for row in rows:
            out.append(call_card(row, bench_by_league.get(row["league_id"], []),
                                 urgent=urgent))

    if thursday:
        out.append(
            "<div class='card trouble'><p class='paneltop'>"
            f"{len(thursday)} decision{'s' if len(thursday) != 1 else ''} "
            "for Thursday night</p><p class='why'>These players kick off "
            "before the rest of the week, so they have to be settled first. "
            "Everything below them can wait until Sunday morning.</p></div>")
        section("Playing Thursday", thursday, True,
                f"{len(thursday)} to settle")
    section("Fix these", urgent, True, f"{len(urgent)} left")
    section("Close calls", close, False, f"{len(close)} left")
    if not (urgent or close or thursday):
        out.append("<h2><span>Nothing to change</span></h2>"
                   "<p class='empty'>"
                   + ("Every call this week has closed; their games have "
                      "started." if closed else
                      "Every starter matches where analysts have them.")
                   + "</p>")
    section("Already played", closed, False, "nothing to do")

    playing = sum(1 for r in started if r["locked"])
    out.append("<h2><span>Full lineups</span>"
               + (f"<span class='meta'>{playing} already playing</span>"
                  if playing else "") + "</h2>")
    seen = []
    for r in started:
        if (r["league_id"], r["league_name"]) not in seen:
            seen.append((r["league_id"], r["league_name"]))
    for lid, lname in seen:
        mine = [r for r in started if r["league_id"] == lid]
        out.append(league_fold(lname or "", mine, bench_by_league.get(lid, []),
                               sum(1 for r in mine if r in urgent or r in close)))

    if done:
        out.append(f"<h2><span>Settled</span><span class='meta'>{len(done)}"
                   "</span></h2>")
        for row in done:
            out.append(
                "<div class='call'><div class='calltop'>"
                f"<div><h3>{e(short_name(row['player_name']))}</h3></div>"
                f"<span class='where'>{e(row['league_name'] or '')} &middot; "
                f"{e(row['slot'] or '')}</span></div>"
                f"<div class='callfoot'><span class='done'>Marked done</span>"
                f"{undo_form(row)}</div></div>")

    out.append("<form method='post' action='/lineup/refresh'>"
               "<button class='ghost' style='width:100%;margin-top:18px'>"
               "Re-check start / sit</button></form>")
    out.append("<p class='foot'>Calls compare your starters against analyst "
               "consensus rankings. Projected points are shown for context "
               "and are never part of a verdict. A slot stops being a call "
               "once its game kicks off.</p>")
    return page("".join(out), "Spike \u2014 start / sit")


# The page works without this. It only says that a slow request is under
# way, which a plain form post cannot express on its own: the start/sit
# check fetches six ranking pages and a player database, and an unmarked
# wait reads as a dead link.
BUSY_JS = """
document.addEventListener('submit', function (e) {
  document.body.classList.add('busy');
  var button = e.submitter || e.target.querySelector('button');
  // Reordering and Undo are small controls whose whole meaning is their
  // label; swapping it for 'Working...' loses that and resizes the row.
  if (!button || button.classList.contains('movebtn')
              || button.classList.contains('link')) { return; }
  // The wait matters. A form's data is built AFTER this handler returns, and
  // disabled controls are left out of it - so disabling the button that was
  // just pressed deletes its own name and value from the request. That is
  // how Approve sent a post with no action in it and the page came back with
  // the card untouched. Deferring puts the change after the data is built.
  setTimeout(function () {
    button.disabled = true;
    button.textContent = 'Working...';
  }, 0);
});
document.addEventListener('click', function (e) {
  var link = e.target.closest('nav a');
  if (link && !link.classList.contains('on')) {
    document.body.classList.add('busy');
  }
});

// What pressing Approve will spend, and what the league has left after it.
// Both follow the box as it is typed in: a budget line that describes the
// bids the run suggested rather than the bids on screen is worse than none.
function approveButton(box) {
  var id = box.getAttribute('form');
  return document.querySelector('[data-approve][form="' + id + '"]');
}
function recost(league) {
  if (!league) { return; }
  var bar = document.querySelector('.bar[data-league="' + league + '"]');
  if (!bar) { return; }
  var boxes = document.querySelectorAll(
    '[data-bid][data-league="' + league + '"]');
  var total = 0;
  for (var i = 0; i < boxes.length; i++) {
    total += parseInt(boxes[i].value, 10) || 0;
  }
  var budget = parseInt(bar.dataset.budget, 10) || 0;
  var committed = parseInt(bar.dataset.committed, 10) || 0;
  var cost = bar.querySelector('[data-cost]');
  var after = bar.querySelector('[data-after]');
  var over = bar.querySelector('[data-over]');
  if (cost) { cost.textContent = total; }
  if (after) { after.textContent = budget - committed - total; }
  if (over) { over.hidden = !(budget && committed + total > budget); }
}
document.addEventListener('input', function (e) {
  var box = e.target.closest('[data-bid]');
  if (!box) { return; }
  var button = approveButton(box);
  if (button) {
    var span = button.querySelector('[data-approve-bid]');
    if (span) { span.textContent = box.value || 0; }
  }
  recost(box.dataset.league);
});

// Choosing a drop closes the list and restates the pick in the one line
// that is always visible, so the card never disagrees with itself.
document.addEventListener('change', function (e) {
  var radio = e.target.closest('.opt input[type=radio]');
  if (!radio) { return; }
  var picker = radio.closest('.picker');
  if (!picker) { return; }
  var name = picker.querySelector('[data-dropname]');
  var pos = picker.querySelector('[data-droppos]');
  if (name) { name.textContent = radio.dataset.name || ''; }
  if (pos) {
    var chip = radio.closest('.opt').querySelector('.pos');
    pos.innerHTML = chip ? chip.outerHTML : '';
  }
  var card = picker.closest('.card');
  var who = card && card.querySelector('[data-dropwho]');
  var why = card && card.querySelector('[data-dropwhy]');
  if (who) { who.textContent = radio.dataset.name || ''; }
  if (why) {
    why.textContent = radio.dataset.why ? ' \u2014 ' + radio.dataset.why : '';
  }
  picker.open = false;
});
window.addEventListener('pageshow', function () {
  document.body.classList.remove('busy');
});
"""


def scoreline(team, leading):
    if not team:
        return ("<div class='score'><span class='team'>No opponent this week"
                "</span></div>")
    return (f"<div class='score{' up' if leading else ''}'>"
            f"<span class='team'>{e(team['name'])}</span>"
            f"<span class='pts'>{team['points']:g}</span></div>"
            f"<div class='projline'><span>{len(team['to_play'])} to play"
            + (f" ({e(', '.join(team['to_play']))})" if team["to_play"] else "")
            + f"</span><span>{team['projected']:g} projected</span></div>")


def half(player, mine):
    """One side of a paired lineup row."""
    if not player:
        return "<div class='half'></div>"
    live = f"{player['points']:g}"
    if player["to_play"]:
        expected = (f"{player['projection']:g} proj"
                    if player["projection"] is not None else "to play")
        under = " &middot; ".join(x for x in (e(player["matchup"] or ""),
                                              e(expected)) if x)
    else:
        under = e(player["matchup"] or "")
    return (f"<div class='half{' them' if not mine else ''}"
            f"{' pending' if player['to_play'] else ''}'>"
            f"{headshot(player)}"
            f"<span class='who'><span class='pname'>{e(player['name'])}</span>"
            f"<span class='under'>{under}</span></span>"
            f"<span class='pts small'>{live}</span></div>")


def matchup_rows(board):
    out = []
    for row in board["rows"]:
        out.append("<div class='pair'>"
                   + half(row["mine"], True)
                   + f"<span class='slotchip'>{e(row['slot'])}</span>"
                   + half(row["theirs"], False)
                   + "</div>")
    return "".join(out)


def render_scores(username):
    """Every league's live matchup on one page.

    Read straight from Sleeper on each load rather than stored: a scoreboard
    that is minutes old is not a scoreboard.
    """
    out = [nav("/scores"), "<h1>Scores</h1>"]
    if not username:
        return page("".join(out) + "<p class='empty'>FANTASY_USER is not set "
                    "on the host, so I cannot look up your leagues.</p>",
                    "Spike \u2014 scores")
    try:
        got = scores.board(username)
    except Exception as exc:
        traceback.print_exc()
        return page("".join(out) + f"<p class='empty'>{e(scrub(exc))}</p>",
                    "Spike \u2014 scores")

    out.append(f"<p class='sub'>Week {e(got['week'])} &middot; live from "
               "Sleeper</p>")
    if not got["leagues"]:
        out.append("<p class='empty'>No matchups found for this week.</p>")

    for b in got["leagues"]:
        us, them, margin = b["us"], b["them"], b["margin"]
        ahead = margin is not None and margin > 0
        if margin is None:
            verdict = ""
        elif margin:
            verdict = (f"{'ahead' if ahead else 'behind'} by {abs(margin):g}")
        else:
            verdict = "level"
        # Where it is heading matters more than where it is when half the
        # lineup has not kicked off.
        end = b["projected_margin"]
        if end is not None and verdict:
            verdict += (f", projected to {'win' if end > 0 else 'lose'} by "
                        f"{abs(end):g}" if end else ", projected to tie")
        out.append(
            "<article class='match'>"
            f"<div class='calltop'><div><h3>{e(b['league_name'] or '')}</h3>"
            f"<p class='why'>{e(verdict)}</p></div></div>"
            + scoreline(us, ahead)
            + scoreline(them, margin is not None and margin < 0)
            + "<details class='others'><summary>Both lineups</summary>"
            + matchup_rows(b) + "</details></article>")

    out.append("<form method='get' action='/scores'>"
               "<button class='ghost' style='width:100%;margin-top:18px'>"
               "Refresh</button></form>")
    return page("".join(out), "Spike \u2014 scores")


def trade_card(offer, players):
    """One offer, headed by the swap and then by what it costs your lineup."""
    send = ", ".join(trades.short(players, p) for p in offer["give"])
    get = ", ".join(trades.short(players, p) for p in offer["get"])
    wins, losses, ties = offer["their_record"]
    record = f"{wins}-{losses}" + (f"-{ties}" if ties else "")

    rows = "".join(f"<div class='swap'><span class='swaplabel'>{label}</span>"
                   f"<span>{e(who)}</span></div>"
                   for label, who in (("You send", send), ("You get", get)))
    changes = "".join(f"<li>{e(line)}</li>"
                      for line in trades.lineup_changes(offer, players))
    return ("<article class='call close'>"
            "<div class='calltop'>"
            f"<div><h3>{e(get)}</h3></div>"
            f"<span class='where'>{e(offer['with'])} &middot; {e(record)}"
            "</span></div>"
            + rows
            + (f"<p class='rowlabel'>Your lineup</p><ul class='changes'>"
               f"{changes}</ul>" if changes else "")
            + f"<p class='why'>Your lineup +{offer['my_pct']}%, theirs "
              f"+{offer['their_pct']}%. "
              f"{e(trades.describe(offer, players, {}))}</p>"
            "</article>")


def refresh_trades(conn, db_path):
    """Work the offers out now and store them. Returns an error, or None."""
    username = os.environ.get("FANTASY_USER", "")
    if not username:
        return "FANTASY_USER is not set on the host, so I cannot look up " \
               "your leagues."
    try:
        import waiver_analyzer as wa
        got = trades.board(username, protect=wa.never_drop_names())
        st.write_trade_run(conn, got["season"], got["week"], got["source"],
                           trades.written_out(got, sc.all_players()))
        return None
    except Exception as exc:
        traceback.print_exc()
        return scrub(f"{type(exc).__name__}: {exc}")


def render_trades(conn):
    """Offers worth sending, read from the last run rather than worked out.

    Building them touches a value list, the player database and every roster
    in every league, which is a scheduled job's work rather than something
    to do while a page loads.
    """
    run = st.latest_trade_run(conn)
    out = [nav("/trades"), "<h1>Trades</h1>"]
    if not run:
        return page("".join(out) + "<p class='empty'>No offers worked out "
                    "yet. They run Tuesday, with the waiver job.</p>"
                    + refresh_trades_button(), "Spike \u2014 trades")

    leagues = st.trade_leagues(conn, run["id"])
    out.append(f"<p class='sub'>Week {e(run['week'])} &middot; worked out "
               f"{e(said_ago(age_of(run)))} &middot; values from "
               f"{e(run['source'] or 'nowhere')}</p>")
    if not any(l["offers"] for l in leagues):
        out.append("<p class='empty'>No package makes both sides better right "
                   "now. That is the usual answer; a trade needs two rosters "
                   "shaped the opposite way.</p>")

    for league in leagues:
        settings = league.get("settings") or {}
        priced = (f"Priced as {settings.get('teams', '?')} teams, "
                  f"{settings.get('ppr', '?')} PPR, "
                  f"{settings.get('quarterbacks', 1)} QB.")
        if settings.get("pass_td") not in (None, 4):
            priced += (f" This league gives {settings['pass_td']} for a "
                       "passing touchdown, which lifts quarterbacks; the "
                       "value list has no setting for it, so read quarterback "
                       "prices here as low.")
        out.append(f"<h2><span>{e(league['league_name'] or '')}</span>"
                   f"<span class='meta'>{len(league['offers'])} offers</span>"
                   "</h2>"
                   f"<div class='bar'>{e(league.get('summary', ''))}</div>"
                   f"<p class='guide'>{e(priced)}</p>")
        for offer in league["offers"]:
            out.append(stored_trade_card(offer))

    out.append(refresh_trades_button())
    out.append("<p class='foot'>Nothing is ever sent. These are packages "
               "where your best starting lineup improves and theirs does too, "
               "which is what makes an offer worth sending rather than merely "
               "worth wanting. The paragraph above each league describes the "
               "roster; byes and records are in it for you to read, not for "
               "the packages to be built from.</p>")
    return page("".join(out), "Spike \u2014 trades")


def refresh_trades_button():
    return ("<form method='post' action='/trades/refresh'>"
            "<button class='ghost' style='width:100%;margin-top:18px'>"
            "Work out trades now</button></form>")


def stored_trade_card(offer):
    send, get = ", ".join(offer["send"]), ", ".join(offer["get"])
    rows = "".join(f"<div class='swap'><span class='swaplabel'>{label}</span>"
                   f"<span>{e(who)}</span></div>"
                   for label, who in (("You send", send), ("You get", get)))
    changes = "".join(f"<li>{e(line)}</li>" for line in offer["changes"])
    return ("<article class='call close'>"
            "<div class='calltop'>"
            f"<div><h3>{e(get)}</h3></div>"
            f"<span class='where'>{e(offer['with'])} &middot; "
            f"{e(offer['their_record'])}</span></div>"
            + rows
            + (f"<p class='rowlabel'>Your lineup</p><ul class='changes'>"
               f"{changes}</ul>" if changes else "")
            + f"<p class='why'>Your lineup +{e(offer['my_pct'])}%, theirs "
              f"+{e(offer['their_pct'])}%. {e(offer['verdict'])}</p>"
            "</article>")


def page(body, title):
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<link rel='icon' href=\"{FAVICON}\">"
            f"<title>{e(title)}</title><style>{CSS}</style></head>"
            f"<body><div class='progress'></div><div class='wrap'>{body}</div>"
            f"<script>{BUSY_JS}</script></body></html>").encode()


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
                # What is actually in the database the page reads. "The job
                # said it wrote six" and "the page shows none" have to be
                # answerable without guessing which end is wrong.
                counts = []
                for label, table in (("runs", "runs"),
                                     ("proposals", "proposals"),
                                     ("lineup checks", "lineup_checks"),
                                     ("trade runs", "trade_runs")):
                    try:
                        row = conn.execute(
                            f"SELECT COUNT(*) AS n FROM {table}").fetchone()
                        counts.append(f"{row['n']} {label}")
                    except Exception:
                        counts.append(f"no {label} table")
                conn.close()
                state = "ok"
                if self._authed() or auth.check_api_token(
                        self.headers.get("Authorization")):
                    state += " — " + ", ".join(counts)
            except Exception as exc:
                traceback.print_exc()
                # The type alone named three different faults across three
                # deploys, so the message has to be here. It names the
                # database host and the schema, so only someone who has
                # signed in or holds the API token gets to read it.
                backend = db.backend()
                state = f"database unreachable: {type(exc).__name__}"
                if self._authed() or auth.check_api_token(
                        self.headers.get("Authorization")):
                    state += f": {scrub(exc)}"
                else:
                    state += " (sign in or send the API token for the detail)"
            self._send(200, f"{state} (db={backend})", "text/plain")
            return
        if path == "/login":
            self._send(200, page(login_page(
                "Sign in to review this week's waiver proposals."), "Spike"))
            return
        if path == "/api/status":
            if not auth.check_api_token(self.headers.get("Authorization")):
                self._json(401, {"error": auth.token_complaint(
                    self.headers.get("Authorization"))})
                return
            conn = self._conn()
            try:
                self._json(200, run_summary(conn))
            except Exception as exc:
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            finally:
                conn.close()
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
        if path == "/trades":
            if not self._authed():
                self.send_response(303)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            conn = self._conn()
            try:
                body = render_trades(conn)
            finally:
                conn.close()
            self._send(200, body.decode())
            return

        if path == "/scores":
            if not self._authed():
                self.send_response(303)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            body = render_scores(os.environ.get("FANTASY_USER", ""))
            self._send(200, body.decode())
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
                self._send(401, page(login_page(
                    "That password was not right. Try again."), "Spike"))
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
                current = st.latest_run(conn)
                fresh = already_ran_this_week(conn)
                if fresh and not (payload.get("force")
                                  or payload.get("update")):
                    self._json(200, {"ok": True,
                                     "skipped": "already ran this week"})
                    return
                if payload.get("update") and fresh and current:
                    # Add to the week's run rather than replacing it. Monday
                    # night only changes the picture a little, and starting
                    # over would hide every decision made during the day.
                    run_id = current["id"]
                    added = "added to"
                else:
                    run_id = st.start_run(conn, payload.get("season"),
                                          payload.get("week"),
                                          payload.get("sources") or [],
                                          note=payload.get("note") or "")
                    added = "filed as"
                written = sum(1 for r in rows
                              if st.add_proposal(conn, run_id, **r) is not None)
                self._json(200, {"ok": True, "run": run_id, "mode": added,
                                 "written": written, "received": len(rows)})
            except Exception as exc:
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            finally:
                conn.close()
            return

        if path == "/api/submit/claim":
            if not auth.check_api_token(self.headers.get("Authorization")):
                self._json(401, {"error": auth.token_complaint(
                    self.headers.get("Authorization"))})
                return
            self.rfile.read(length)
            conn = self._conn()
            try:
                waiting = st.pending_submit_request(conn)
                if not waiting:
                    self._json(200, {"requested": False})
                    return
                st.claim_submit_request(conn, waiting["id"])
                self._json(200, {"requested": True, "id": waiting["id"],
                                 "asked_at": waiting["asked_at"]})
            finally:
                conn.close()
            return

        if path == "/api/submit/done":
            if not auth.check_api_token(self.headers.get("Authorization")):
                self._json(401, {"error": auth.token_complaint(
                    self.headers.get("Authorization"))})
                return
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json(400, {"error": "body was not JSON"})
                return
            conn = self._conn()
            try:
                st.finish_submit_request(conn, int(payload.get("id") or 0),
                                         payload.get("detail", ""))
                self._json(200, {"ok": True})
            finally:
                conn.close()
            return

        if path == "/api/trades":
            if not auth.check_api_token(self.headers.get("Authorization")):
                self._json(401, {"error": auth.token_complaint(
                    self.headers.get("Authorization"))})
                return
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json(400, {"error": "body was not JSON"})
                return
            leagues = payload.get("leagues") or []
            conn = self._conn()
            try:
                run = st.write_trade_run(
                    conn, payload.get("season"), payload.get("week"),
                    payload.get("source"), leagues)
                self._json(200, {"ok": True, "run": run,
                                 "written": len(leagues)})
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
                check_id = st.write_lineup_check(
                    conn, payload.get("season"), payload.get("week"),
                    payload.get("sources") or [], rows)
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
                detail = str(body.get("detail", ""))[:500]
                outcome = body.get("outcome")
                if outcome in (st.WON, st.LOST):
                    st.settle_claim(conn, pid, outcome == st.WON, detail)
                elif body.get("submitted"):
                    st.mark_submitted(conn, pid, bool(body.get("ok")), detail)
                elif body.get("settled"):
                    # The league has decided this one: the player is on
                    # somebody's roster. Retiring it keeps the queue to
                    # claims that could still happen.
                    st.retire(conn, pid, detail)
                else:
                    # Nothing reached the league, so keep it retryable.
                    st.log(conn, "attempt_failed", detail, pid)
                    conn.commit()
                self._json(200, {"ok": True})
            finally:
                conn.close()
            return

        if path == "/submit":
            if not self._authed():
                self.send_response(303)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            self.rfile.read(length)
            conn = self._conn()
            try:
                st.ask_to_submit(conn)
            finally:
                conn.close()
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
            return

        if path == "/trades/refresh":
            if not self._authed():
                self.send_response(303)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            self.rfile.read(length)
            conn = self._conn()
            try:
                refresh_trades(conn, self.db_path)
            finally:
                conn.close()
            self.send_response(303)
            self.send_header("Location", "/trades")
            self.end_headers()
            return

        if path == "/fallback":
            if not self._authed():
                self.send_response(303)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            form = parse_qs(self.rfile.read(length).decode("utf-8"))
            pid = (form.get("id") or [None])[0]
            conn = self._conn()
            try:
                try:
                    st.add_fallback(conn, int(pid))
                except (TypeError, ValueError):
                    pass
            finally:
                conn.close()
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
            return

        if path == "/order":
            if not self._authed():
                self.send_response(303)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            form = parse_qs(self.rfile.read(length).decode("utf-8"))
            pid = (form.get("id") or [None])[0]
            direction = (form.get("dir") or [""])[0]
            conn = self._conn()
            try:
                try:
                    st.reorder(conn, int(pid), direction)
                except (TypeError, ValueError):
                    pass  # a mangled form moves nothing
            finally:
                conn.close()
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
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

        if path in ("/lineup/done", "/lineup/undo"):
            if not self._authed():
                self.send_response(303)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            form = parse_qs(self.rfile.read(length).decode("utf-8"))
            conn = self._conn()
            try:
                check = st.latest_lineup_check(conn)
                if check:
                    act = st.settle if path.endswith("done") else st.unsettle
                    act(conn, check["season"], check["week"],
                        (form.get("league") or [""])[0],
                        (form.get("slot") or [""])[0],
                        (form.get("player") or [""])[0])
            finally:
                conn.close()
            self.send_response(303)
            self.send_header("Location", "/lineup")
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
        drop = (form.get("drop") or [None])[0]

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
                    pick = drop_pick(conn, pid, drop) or {}
                    st.decide(conn, int(pid), status, bid=bid_val,
                              drop_player_id=drop or None,
                              drop_player_name=pick.get("name"),
                              drop_position=pick.get("position"))
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
