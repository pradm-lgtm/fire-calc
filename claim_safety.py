#!/usr/bin/env python3
"""
Pre-flight checks and read-back verification for waiver claims.

Two jobs, both independent of any browser:

PRE-FLIGHT. A proposal is computed Tuesday morning and submitted later, and
the league moves in between. Before touching a browser, re-check against the
live league that the add is still a free agent, the drop is still on your
roster, and the bid still fits the budget. A stale proposal is refused, not
submitted.

READ-BACK. Sleeper's public API does NOT report waiver claims while they are
pending - this was assumed and it is not so. Its transactions endpoint lists
a claim only once waivers have processed it, as complete or failed, which is
why a league with a pending claim sitting in its own UI answers with nothing
at all. So the read-back cannot confirm a claim at the moment it is placed,
and pretending otherwise turned every successful submission into a reported
failure.

What it can do is settle afterwards. Once waivers run, the transaction
appears with its outcome, and that is worth more than a confirmation of
placement: it says whether the player was actually won, or whether somebody
outbid you. The audit reads the league for that, and it is still the browser
automation's account that is not trusted - only later, and for a better
question.
"""

import sleeper_client as sc
import store as st


def waiver_claims(league_id, week):
    """Every waiver transaction Sleeper will admit to, around this week.

    Any status, because pending ones are never here: a claim shows up only
    once it has processed, and then it reads complete or failed. The week
    either side is still worth asking for, since which week a claim lands
    under depends on when it processed rather than when it was placed.
    """
    out = []
    try:
        current = int(week)
    except (TypeError, ValueError):
        current = 1
    for wk in (current, current + 1, max(1, current - 1)):
        rows = sc.get(f"/league/{league_id}/transactions/{wk}") or []
        for t in rows:
            if t.get("type") != "waiver":
                continue
            out.append({
                "week": wk,
                "transaction_id": t.get("transaction_id"),
                "status": t.get("status"),
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


def pending_claims(league_id, week):
    """Kept for callers that still ask. Always empty, and honestly so."""
    return [c for c in waiver_claims(league_id, week)
            if c.get("status") == "pending"]


def my_roster_id(league_id, user_id):
    for r in sc.league_rosters(league_id):
        if str(r.get("owner_id")) == str(user_id):
            return r.get("roster_id")
    return None


def preflight(proposal, user_id, players=None):
    """(ok, reason, settled). Re-verify a proposal against the live league.

    `settled` means the answer cannot change before waivers process, so the
    claim is finished rather than waiting. A player another team now owns is
    settled; a bid that no longer fits the budget is not, because approving
    fewer claims frees it up again. Without the distinction a dead claim sits
    in the approved queue forever, re-checked and re-refused every run, and
    the queue fills with things that can never happen.
    """
    league_id = proposal["league_id"]
    add_id = str(proposal["add_player_id"])
    drop_id = str(proposal["drop_player_id"]) if proposal["drop_player_id"] else None

    rosters = sc.league_rosters(league_id)
    if not rosters:
        return False, "could not read the league's rosters", False

    mine = None
    taken = set()
    for r in rosters:
        for pid in (r.get("players") or []):
            taken.add(str(pid))
        if str(r.get("owner_id")) == str(user_id):
            mine = r
    if mine is None:
        return False, "your roster is not in this league", False

    if add_id in taken:
        mine_ids = {str(p) for p in (mine.get("players") or [])}
        who = "you already have him" if add_id in mine_ids \
            else "another team has him"
        # Nobody gives a player back before waivers run.
        return False, f"{proposal['add_player_name']} is no longer free — {who}", True

    if drop_id and drop_id not in {str(p) for p in (mine.get("players") or [])}:
        return False, (f"{proposal['drop_player_name']} is no longer on your "
                       "roster"), False

    if drop_id and players:
        # Belt and braces: the drop chooser already skips these, but a bad
        # edit on the approval page must not get one cut either.
        import waiver_analyzer as wa
        if wa.is_protected(players.get(drop_id)):
            return False, (f"{proposal['drop_player_name']} is on your "
                           "never-drop list"), True

    note = "ok"
    if drop_id and drop_id in {str(p) for p in (mine.get("starters") or []) if p}:
        # Not a refusal any more. The drop is chosen by name on the approval
        # page, starters included, because dropping one is normal when the
        # man you are adding is better than him. Refusing it here would
        # overrule the decision the whole system exists to record - but it
        # is worth saying out loud before the claim goes in.
        note = (f"{proposal['drop_player_name']} is in your starting lineup "
                "— dropping a starter, as approved")

    bid = proposal["bid"]
    if bid is not None:
        settings = mine.get("settings") or {}
        budget = proposal["max_bid"]
        used = settings.get("waiver_budget_used")
        if used is not None and budget:
            left = budget - used
            if bid > left:
                return False, (f"bid {bid} exceeds the {left} FAAB you have "
                               "left"), False
        if bid < 0:
            return False, "negative bid", True
    return True, note, False


def verify_submitted(conn, proposal, user_id, week):
    """(settled, detail) - what the league says about this claim, if anything.

    settled is False while the claim is merely placed. That is the normal
    state for a claim made before waivers run, not a problem: Sleeper does
    not list pending claims, so there is nothing to find yet and nothing is
    wrong. It turns True once the claim has processed, whether it was won or
    lost, because either way the league has finished with it.
    """
    league_id = proposal["league_id"]
    roster_id = my_roster_id(league_id, user_id)
    add_id = str(proposal["add_player_id"])

    mine = []
    for claim in waiver_claims(league_id, week):
        if roster_id is not None and roster_id not in claim["roster_ids"]:
            continue
        if add_id not in {str(k) for k in claim["adds"]}:
            continue
        mine.append(claim)

    want_drop = (str(proposal["drop_player_id"])
                 if proposal["drop_player_id"] else None)
    exact = [c for c in mine
             if want_drop and want_drop in {str(k) for k in c["drops"]}]
    for claim in (exact or mine):
        detail = f"{claim['status']} (week {claim['week']})"
        if claim["bid"] is not None:
            detail += f", bid {claim['bid']}"
            if proposal["bid"] is not None and claim["bid"] != proposal["bid"]:
                detail += f" (approved {proposal['bid']} — MISMATCH)"
        return True, detail

    return False, ("not processed yet — Sleeper lists a waiver claim only "
                   "after it runs, so a claim placed now is invisible here "
                   "until then")


def won(detail):
    """Did a settled claim actually get the player?"""
    return str(detail or "").startswith("complete")


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
