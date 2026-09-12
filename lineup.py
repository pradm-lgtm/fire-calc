#!/usr/bin/env python3
"""
Start/sit recommendations (the other half of the original brief).

    python3 lineup.py <your-sleeper-username>
    python3 lineup.py pradm7 --week 3

Compares who you have starting against who is on your bench, using
projections scored by each league's own rules. That last part matters more
than it sounds: your two leagues award 4 and 6 points for a passing
touchdown, so the same quarterback is worth visibly different amounts in
each, and a single ranking would be wrong in one of them.

Flags three things, in descending order of how much they should worry you:

  * a starter who is Out or Doubtful - points you are simply forfeiting
  * a bench player projected clearly above a starter he could replace
  * a starter who is Questionable, which is a judgement call, not a verdict

Recommendations only. Nothing is changed in any league.
"""

import argparse
import sys

import sleeper_client as sc
import waiver_analyzer as wa

# Which positions may fill which lineup slot.
SLOT_ELIGIBILITY = {
    "QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"},
    "K": {"K"}, "DEF": {"DEF"},
    "FLEX": {"RB", "WR", "TE"},
    "WRRB_FLEX": {"RB", "WR"},
    "REC_FLEX": {"WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
}
BENCH_SLOTS = {"BN", "IR", "TAXI"}

# A swap has to be worth more than the noise in a projection to be worth
# making; below this the honest answer is that it does not matter.
MEANINGFUL_POINTS = 1.5

SIT_STATUSES = {"out", "ir", "doubtful", "suspended", "pup"}


def get_projections(season, week):
    """{player_id: {stat: value}} for the week, or {} if unavailable.

    This endpoint is not part of Sleeper's documented API, so treat its
    absence as normal rather than an error - the tool still has injury
    information to work with.
    """
    try:
        rows = sc.get(f"/projections/nfl/{season}/{week}"
                      f"?season_type=regular&order_by=ppr")
    except sc.SleeperError:
        return {}
    if not isinstance(rows, list):
        return {}
    out = {}
    for r in rows:
        pid = r.get("player_id")
        stats = r.get("stats")
        if pid and isinstance(stats, dict):
            out[str(pid)] = stats
    return out


def project_points(stats, scoring):
    """Score a projected stat line by one league's own rules."""
    if not stats or not scoring:
        return None
    total = 0.0
    for key, value in stats.items():
        weight = scoring.get(key)
        if weight is None:
            continue
        try:
            total += float(weight) * float(value)
        except (TypeError, ValueError):
            continue
    return round(total, 2)


def injury_note(player):
    status = (player.get("injury_status") or "").strip()
    return status or ""


def must_sit(player):
    return injury_note(player).lower() in SIT_STATUSES


def starting_slots(league):
    return [s for s in (league.get("roster_positions") or [])
            if s not in BENCH_SLOTS]


def eligible(player, slot):
    pos = player.get("position")
    allowed = SLOT_ELIGIBILITY.get(slot)
    if not allowed:
        return False
    if pos in allowed:
        return True
    return bool(set(player.get("fantasy_positions") or []) & allowed)


def evaluate(league, roster, players, projections):
    """Return (rows, swaps) describing the lineup and what to change."""
    scoring = league.get("scoring_settings") or {}
    slots = starting_slots(league)
    starters = [p for p in (roster.get("starters") or []) if p and p != "0"]
    everyone = [str(p) for p in (roster.get("players") or []) if p and p != "0"]
    bench = [p for p in everyone if p not in starters]

    def points(pid):
        return project_points(projections.get(str(pid)), scoring)

    rows = []
    for i, pid in enumerate(starters):
        slot = slots[i] if i < len(slots) else "?"
        player = players.get(str(pid)) or {}
        rows.append({"slot": slot, "pid": str(pid), "player": player,
                     "points": points(pid), "injury": injury_note(player)})

    bench_rows = [{"pid": p, "player": players.get(p) or {}, "points": points(p),
                   "injury": injury_note(players.get(p) or {})} for p in bench]

    swaps, used = [], set()
    for row in rows:
        best, best_gain = None, 0.0
        for cand in bench_rows:
            if cand["pid"] in used or not cand["player"]:
                continue
            if not eligible(cand["player"], row["slot"]):
                continue
            if must_sit(cand["player"]):
                continue
            # An unavailable starter should be replaced by anyone playable,
            # even where projections cannot say by how much.
            if must_sit(row["player"]):
                gain = (cand["points"] or 0) - 0
                if best is None or gain > best_gain:
                    best, best_gain = cand, gain
                continue
            if row["points"] is None or cand["points"] is None:
                continue
            gain = cand["points"] - row["points"]
            if gain > best_gain:
                best, best_gain = cand, gain
        if best is None:
            continue
        forced = must_sit(row["player"])
        if forced or best_gain >= MEANINGFUL_POINTS:
            used.add(best["pid"])
            swaps.append({"row": row, "with": best, "gain": best_gain,
                          "forced": forced})
    return rows, swaps


def report(league, user_id, players, projections):
    print()
    print("=" * 74)
    print(f"{league.get('name','?')}   ({sc.scoring_summary(league)})")
    print("=" * 74)

    rosters = sc.league_rosters(league["league_id"])
    mine = sc.my_roster(rosters, user_id)
    if not mine:
        print("Could not find your roster here; skipping.")
        return 0

    rows, swaps = evaluate(league, mine, players, projections)
    have_points = any(r["points"] is not None for r in rows)

    print("\nYOUR LINEUP" + ("" if have_points else "   (no projections available)"))
    for r in rows:
        pts = f"{r['points']:6.1f}" if r["points"] is not None else "     -"
        print(f"  {r['slot']:<11} {pts}  {sc.player_label(players, r['pid'])}")

    if not swaps:
        print("\nNo changes worth making.")
        if not have_points:
            print("Projections were unavailable, so this only checked injuries.")
        return 0

    print(f"\nSUGGESTED CHANGES ({len(swaps)}):")
    for s in swaps:
        # The label carries the injury flag, so name him plainly here and let
        # the sentence say why.
        out_name = sc.player_label(players, s["row"]["pid"]).split(" [")[0]
        in_name = sc.player_label(players, s["with"]["pid"])
        print()
        if s["forced"]:
            print(f"  {s['row']['slot']}: {out_name} is "
                  f"{s['row']['injury']} — he will not play")
            print(f"      start {in_name} instead")
            if s["with"]["points"] is not None:
                print(f"      projected {s['with']['points']:.1f}")
        else:
            print(f"  {s['row']['slot']}: start {in_name} over {out_name}")
            print(f"      {s['with']['points']:.1f} vs {s['row']['points']:.1f}"
                  f"  (+{s['gain']:.1f} projected)")
            if s["row"]["injury"]:
                print(f"      {out_name} is also {s['row']['injury']}")
    return len(swaps)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("username")
    ap.add_argument("--week", type=int, default=None)
    args = ap.parse_args()

    try:
        state = sc.current_state()
        season = state.get("season")
        week = args.week or state.get("week") or 1
        print(f"NFL {season}, week {week}")

        user = sc.resolve_user(args.username)
        leagues = sc.user_leagues(user["user_id"], season)
        if not leagues:
            print("No leagues found.")
            return 1
        players = sc.all_players()
        projections = get_projections(season, week)
        print(f"Projections for {len(projections):,} players."
              if projections else
              "Projections unavailable — checking injuries only.")

        total = sum(report(l, user["user_id"], players, projections)
                    for l in leagues)
        print()
        print("=" * 74)
        print(f"{total} change(s) suggested. Nothing was altered in any league.")
        print("=" * 74)
        return 0
    except sc.SleeperError as e:
        print(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
