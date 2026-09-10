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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import store as st

CSS = """
:root { color-scheme: light dark; --bg:#f6f7f9; --card:#fff; --ink:#12141a;
        --muted:#5c6370; --line:#e3e6ea; --ok:#0a7d28; --no:#b3261e;
        --accent:#1a56db; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0f1115; --card:#181b21; --ink:#e8eaed; --muted:#9aa2ad;
          --line:#272b33; --ok:#4ade80; --no:#f87171; --accent:#7aa2f7; }
}
* { box-sizing:border-box; }
body { margin:0; padding:16px; background:var(--bg); color:var(--ink);
       font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
.wrap { max-width:760px; margin:0 auto; }
h1 { font-size:20px; margin:0 0 4px; }
.sub { color:var(--muted); font-size:13px; margin-bottom:20px; }
h2 { font-size:16px; margin:26px 0 10px; padding-bottom:6px;
     border-bottom:1px solid var(--line); }
.meta { color:var(--muted); font-size:12px; font-weight:normal; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px;
        padding:14px; margin-bottom:12px; }
.move { display:flex; gap:10px; align-items:baseline; flex-wrap:wrap; }
.tag { font-size:11px; font-weight:700; letter-spacing:.04em; padding:2px 6px;
       border-radius:4px; background:var(--line); color:var(--muted); }
.add { font-weight:650; }
.drop { color:var(--muted); }
.why { color:var(--muted); font-size:13px; margin:8px 0 0; }
.quote { border-left:3px solid var(--line); padding-left:10px; margin:8px 0 0;
         color:var(--muted); font-size:13px; font-style:italic; }
form.row { display:flex; gap:8px; align-items:center; margin-top:12px;
           flex-wrap:wrap; }
input[type=number] { width:74px; padding:7px; border:1px solid var(--line);
                     border-radius:6px; background:var(--bg); color:var(--ink); }
select { padding:7px; border:1px solid var(--line); border-radius:6px;
         background:var(--bg); color:var(--ink); max-width:100%; }
button { padding:8px 14px; border-radius:6px; border:1px solid transparent;
         font-weight:600; cursor:pointer; font-size:14px; }
.approve { background:var(--ok); color:#fff; }
.decline { background:transparent; color:var(--no); border-color:var(--line); }
.state { font-size:13px; font-weight:600; }
.state.approved { color:var(--ok); }
.state.declined { color:var(--no); }
.state.submitted { color:var(--accent); }
.empty { color:var(--muted); padding:24px 0; }
.bar { background:var(--card); border:1px solid var(--line); border-radius:8px;
       padding:10px 12px; font-size:13px; color:var(--muted); margin-bottom:8px; }
.warn { color:var(--no); font-weight:600; }
label { font-size:13px; color:var(--muted); }
"""


def e(v):
    return html.escape("" if v is None else str(v), quote=True)


def render(conn):
    run = st.latest_run(conn)
    if not run:
        return page("<p class='empty'>No runs yet. Run the weekly job first:"
                    "<br><code>python3 run_weekly.py</code></p>", "Waivers")

    rows = st.proposals_for_run(conn, run["id"])
    if not rows:
        return page("<p class='empty'>That run produced no proposals.</p>",
                    "Waivers")

    by_league = {}
    for r in rows:
        by_league.setdefault((r["league_id"], r["league_name"]), []).append(r)

    out = [f"<h1>Waiver proposals</h1>"
           f"<div class='sub'>Week {e(run['week'])} &middot; generated "
           f"{e(run['created_at'])} &middot; nothing is submitted until you "
           f"approve it</div>"]

    for (lid, lname), items in by_league.items():
        budget = items[0]["max_bid"] or 0
        committed = st.budget_committed(conn, lid)
        bar = (f"FAAB committed here: <strong>{committed}</strong>"
               f"{f' of {budget}' if budget else ''}")
        if budget and committed > budget:
            bar += " <span class='warn'>— over budget</span>"
        out.append(f"<h2>{e(lname)} <span class='meta'>{len(items)} proposed"
                   f"</span></h2><div class='bar'>{bar}</div>")
        for r in items:
            out.append(card(r))
    return page("".join(out), "Waiver proposals")


def card(r):
    srcs = json.loads(r["sources"] or "[]")
    src_txt = ", ".join(short_source(s) for s in srcs) or "—"
    bits = [f"<div class='card'>",
            "<div class='move'>",
            "<span class='tag'>ADD</span>",
            f"<span class='add'>{e(r['add_player_name'])}</span>",
            f"<span class='meta'>{e(r['add_position'] or '')}</span>",
            "</div>"]
    if r["drop_player_name"]:
        bits += ["<div class='move' style='margin-top:6px'>",
                 "<span class='tag'>DROP</span>",
                 f"<span class='drop'>{e(r['drop_player_name'])}</span>",
                 f"<span class='meta'>{e(r['drop_position'] or '')}</span>",
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
            "<label>Bid</label>"
            f"<input type='number' name='bid' min='0' max='{e(r['max_bid'] or 100)}'"
            f" value='{e(r['bid'] if r['bid'] is not None else 0)}'>"
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


def short_source(url):
    try:
        host = urlparse(url).netloc or url
        return host.replace("www.", "")
    except Exception:
        return str(url)


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

    def do_GET(self):
        if urlparse(self.path).path != "/":
            self.send_error(404)
            return
        conn = self._conn()
        try:
            body = render(conn)
        finally:
            conn.close()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urlparse(self.path).path != "/decide":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
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


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--db", default=str(st.DB_PATH))
    args = ap.parse_args()

    Handler.db_path = args.db
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
