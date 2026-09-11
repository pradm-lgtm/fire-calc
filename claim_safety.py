#!/usr/bin/env python3
"""
Pre-flight checks and read-back verification for waiver claims.

Two jobs, both independent of any browser:

PRE-FLIGHT. A proposal is computed Tuesday morning and submitted later, and
the league moves in between. Before touching a browser, re-check against the
live league that the add is still a free agent, the drop is still on your
roster, and the bid still fits the budget. A stale proposal is refused, not
submitted.

READ-BACK. Sleeper's public API reports waiver claims while they are still
pending, so after submitting we can confirm from the outside that the claim
exists and matches what was approved. This is the audit: it does not trust
the browser automation's own account of what it did.
"""

import sleeper_client as sc
import store as st


def pending_claims(league_id, week):
    """Waiver claims that exist but have not processed yet, from the API.

    Sleeper indexes transactions by the week they belong to, and a claim
    placed today belongs to the week it will process in - which is the next
    one, not the current one. Checking only the current week reports a claim
    that plainly exists as missing, so look either side of it.
    """
    out = []
    try:
        current = int(week)
    except (TypeError, ValueError):
        current = 1
    for wk in (current, current + 1, max(1, current - 1)):
        rows = sc.get(f"/league/{league_id}/transactions/{wk}") or []
        for t in rows:
            if t.get("type") != "waiver" or t.get("status") != "pending":
                continue
            out.append({
                "week": wk,
                "transaction_id": t.get("transaction_id"),
                "roster_ids": t.get("roster_ids") or [],
                "adds": t.get("adds") or {},
                "drops": t.get("drops") or {},
                "bid": (t.get("settings") or {}).get("waiver_bid"),
            })
    seen, uniq = set(), []
    for c in out:
        if c["transaction_id"] in seen:
            continue
        seen.add(c["transaction_id"])
        uniq.append(c)
    return uniq


def my_roster_id(league_id, user_id):
    for r in sc.league_rosters(league_id):
        if str(r.get("owner_id")) == str(user_id):
            return r.get("roster_id")
    return None


def preflight(proposal, user_id, players=None):
    """(ok, reason). Re-verify a proposal against the live league."""
    league_id = proposal["league_id"]
    add_id = str(proposal["add_player_id"])
    drop_id = str(proposal["drop_player_id"]) if proposal["drop_player_id"] else None

    rosters = sc.league_rosters(league_id)
    if not rosters:
        return False, "could not read the league's rosters"

    mine = None
    taken = set()
    for r in rosters:
        for pid in (r.get("players") or []):
            taken.add(str(pid))
        if str(r.get("owner_id")) == str(user_id):
            mine = r
    if mine is None:
        return False, "your roster is not in this league"

    if add_id in taken:
        mine_ids = {str(p) for p in (mine.get("players") or [])}
        who = "you already have him" if add_id in mine_ids \
            else "another team has him"
        return False, f"{proposal['add_player_name']} is no longer free — {who}"

    if drop_id and drop_id not in {str(p) for p in (mine.get("players") or [])}:
        return False, f"{proposal['drop_player_name']} is no longer on your roster"

    if drop_id and drop_id in {str(p) for p in (mine.get("starters") or []) if p}:
        return False, (f"{proposal['drop_player_name']} is in your starting "
                       "lineup — refusing to drop a starter")

    bid = proposal["bid"]
    if bid is not None:
        settings = mine.get("settings") or {}
        budget = proposal["max_bid"]
        used = settings.get("waiver_budget_used")
        if used is not None and budget:
            left = budget - used
            if bid > left:
                return False, f"bid {bid} exceeds the {left} FAAB you have left"
        if bid < 0:
            return False, "negative bid"
    return True, "ok"


def verify_submitted(conn, proposal, user_id, week):
    """Confirm from the API that an approved claim really exists in the league.

    Returns (found, detail). Matching is on the added player and the roster,
    which is what identifies a claim; the bid is reported so a mismatch is
    visible rather than silently accepted.
    """
    league_id = proposal["league_id"]
    roster_id = my_roster_id(league_id, user_id)
    add_id = str(proposal["add_player_id"])

    for claim in pending_claims(league_id, week):
        if roster_id is not None and roster_id not in claim["roster_ids"]:
            continue
        if add_id not in {str(k) for k in claim["adds"]}:
            continue
        detail = f"pending claim {claim['transaction_id']}"
        if claim["bid"] is not None:
            detail += f", bid {claim['bid']}"
            if proposal["bid"] is not None and claim["bid"] != proposal["bid"]:
                detail += f" (approved {proposal['bid']} — MISMATCH)"
        drops = {str(k) for k in claim["drops"]}
        want_drop = (str(proposal["drop_player_id"])
                     if proposal["drop_player_id"] else None)
        if want_drop and want_drop not in drops:
            detail += "; drop does not match what was approved"
        return True, detail + " (week %s)" % claim["week"]

    # Say what IS queued, so a mismatch can be told from nothing at all.
    everything = pending_claims(league_id, week)
    if not everything:
        return False, ("no pending waiver claims at all in this league "
                       "(checked weeks around %s)" % week)
    lines = []
    for c in everything:
        mine_flag = "yours" if roster_id in c["roster_ids"] else "another team"
        lines.append("week %s %s adds=%s bid=%s"
                     % (c["week"], mine_flag, list(c["adds"]), c["bid"]))
    return False, ("no claim for this player; %d pending claim(s) exist: %s"
                   % (len(everything), "; ".join(lines[:6])))


def audit(conn, user_id, week):
    """What is actually queued in each league right now, next to what we sent.

    Read from the league, not from our own records, so a claim we believe we
    placed and one that truly exists can be told apart.
    """
    rows = conn.execute(
        "SELECT * FROM proposals WHERE status IN (?, ?) ORDER BY league_id, id",
        (st.SUBMITTED, st.APPROVED)).fetchall()
    report = []
    for r in rows:
        found, detail = verify_submitted(conn, r, user_id, week)
        report.append({
            "proposal_id": r["id"],
            "league": r["league_name"],
            "add": r["add_player_name"],
            "drop": r["drop_player_name"],
            "bid": r["bid"],
            "status": r["status"],
            "live": found,
            "detail": detail,
        })
    return report
