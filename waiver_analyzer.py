#!/usr/bin/env python3
"""
Waiver-wire analyzer (step 3 of the fantasy agent).

For each of your Sleeper leagues: works out who is actually available,
ranks them, and pairs the best adds against your weakest droppable bench
players. Recommendations only — nothing is submitted anywhere.

    python3 waiver_analyzer.py <your-sleeper-username>
    python3 waiver_analyzer.py <username> --league LEHG --top 15

HOW PLAYERS ARE RANKED, AND WHAT THAT IS WORTH
Sleeper's API serves no projections, so this ranks on the signals it does
serve:

  * search_rank    Sleeper's own overall player ranking (lower is better).
                   Decent as a baseline of established value; slow to react.
  * trending adds  How many Sleeper leagues added the player in the last
                   24h. Fast, and the main way breakouts surface — but it
                   is a popularity signal, so it also chases hype.
  * depth chart    Starters are worth more than backups at the same rank.
  * availability   Injured or inactive players are penalized, not hidden.

That combination finds "who is everyone picking up, and is he any good",
which is most of waiver work. It cannot know a coach announced a starter
an hour ago, and it has no concept of your roster's positional need beyond
the crude bench comparison below. Treat the output as a ranked shortlist to
review, not a verdict — the point of this tool is to cut 400 free agents
down to 15 worth thinking about.

Stdlib only. Requires Python 3.8+.
"""

import math
import sys

import sleeper_client as sc

# search_rank beyond this is treated as "not fantasy relevant" (it doubles as
# Sleeper's sentinel for unranked players).
RANK_FLOOR = 2000

# Slots that represent real fantasy contributors; used to filter the player
# pool down from all 12k NFL players (practice squad, retired, etc).
FANTASY_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}

# Injury designations, worst to mildest, as a multiplier on a player's score.
INJURY_PENALTY = {
    "out": 0.25, "ir": 0.1, "pup": 0.1, "sus": 0.1, "susp": 0.1,
    "doubtful": 0.4, "questionable": 0.85,
}


def trending_adds(lookback_hours=24, limit=200):
    """{player_id: add_count} for players being picked up across Sleeper."""
    rows = sc.get(
        f"/players/nfl/trending/add?lookback_hours={lookback_hours}&limit={limit}"
    ) or []
    return {str(r["player_id"]): r.get("count", 0) for r in rows if r.get("player_id")}


def rostered_player_ids(rosters):
    taken = set()
    for r in rosters:
        for pid in (r.get("players") or []):
            if pid and pid != "0":
                taken.add(str(pid))
    return taken


def is_rosterable(player):
    """Filter the 12k-player database down to plausible fantasy contributors."""
    if not player.get("team"):
        return False  # no NFL team: not startable
    positions = set(player.get("fantasy_positions") or [])
    if player.get("position"):
        positions.add(player["position"])
    if not positions & FANTASY_POSITIONS:
        return False
    status = (player.get("status") or "").lower()
    return status in ("", "active", "questionable", "doubtful", "out", "injured reserve")


def availability_multiplier(player):
    status = (player.get("injury_status") or "").strip().lower()
    if not status:
        return 1.0
    for key, mult in INJURY_PENALTY.items():
        if status.startswith(key):
            return mult
    return 0.9


def score_player(player, trend_count):
    """Blend Sleeper's rank, pickup momentum, and depth chart into one number.

    Scaled so a typical waiver add lands in the tens and a genuinely hot
    pickup in the hundreds. The absolute value means nothing; only the
    ordering within a league does.
    """
    # Both terms are put on a 0-100ish scale on purpose. Pickup counts are
    # extremely skewed (a hot add can be 100k while a useful one is 500), so
    # both go through a log: raw counts would swamp every other signal and
    # reduce this to a copy of the trending list.
    rank = player.get("search_rank")
    if not isinstance(rank, int) or rank <= 0 or rank > RANK_FLOOR:
        rank_score = 0.0
    else:
        rank_score = 100.0 * (1.0 - math.log10(rank) / math.log10(RANK_FLOOR))

    trend_score = 20.0 * math.log10(1 + trend_count) if trend_count > 0 else 0.0

    depth = player.get("depth_chart_order")
    if depth == 1:
        depth_mult = 1.25
    elif depth == 2:
        depth_mult = 1.0
    elif isinstance(depth, int) and depth >= 3:
        depth_mult = 0.8
    else:
        depth_mult = 1.0

    raw = (rank_score + trend_score) * depth_mult
    return raw * availability_multiplier(player)


def describe_reason(player, trend_count):
    """Short human explanation of why a player scored where he did."""
    bits = []
    if trend_count:
        bits.append(f"{trend_count:,} adds/24h")
    rank = player.get("search_rank")
    if isinstance(rank, int) and 0 < rank <= 2000:
        bits.append(f"rank #{rank}")
    if player.get("depth_chart_order") == 1:
        bits.append("depth chart starter")
    elif isinstance(player.get("depth_chart_order"), int) and player["depth_chart_order"] >= 3:
        bits.append(f"DC{player['depth_chart_order']}")
    status = player.get("injury_status")
    if status:
        bits.append(status)
    return ", ".join(bits) or "no strong signal"


def rank_free_agents(players, taken, trending, limit):
    out = []
    for pid, player in players.items():
        if pid in taken or not is_rosterable(player):
            continue
        trend = trending.get(pid, 0)
        score = score_player(player, trend)
        if score <= 0:
            continue
        out.append((score, pid, player, trend))
    out.sort(key=lambda row: row[0], reverse=True)
    return out[:limit]


def rank_drop_candidates(players, roster, trending):
    """Your bench, worst first — who you could reasonably cut."""
    _, bench = sc.split_roster(roster)
    rows = []
    for pid in bench:
        player = players.get(str(pid))
        if not player:
            continue
        rows.append((score_player(player, trending.get(str(pid), 0)), str(pid), player))
    rows.sort(key=lambda row: row[0])
    return rows


def analyze_league(league, user_id, players, trending, top_n):
    print()
    print("=" * 72)
    print(f"{league.get('name','?')}   ({league.get('league_id')})")
    print("=" * 72)
    settings = league.get("settings") or {}
    print(f"{settings.get('num_teams','?')} teams | {sc.scoring_summary(league)}")

    rosters = sc.league_rosters(league["league_id"])
    mine = sc.my_roster(rosters, user_id)
    if not mine:
        print("Could not find your roster in this league; skipping.")
        return

    budget = settings.get("waiver_budget")
    used = (mine.get("settings") or {}).get("waiver_budget_used", 0)
    if budget:
        print(f"FAAB remaining: {budget - used} of {budget}")

    taken = rostered_player_ids(rosters)
    available = rank_free_agents(players, taken, trending, top_n)
    print(f"Free agents considered: "
          f"{sum(1 for p, pl in players.items() if p not in taken and is_rosterable(pl)):,}")

    print()
    print(f"TOP {len(available)} AVAILABLE")
    for i, (score, pid, player, trend) in enumerate(available, 1):
        print(f"{i:>3}. {sc.player_label(players, pid):<38} "
              f"{score:>6.1f}  {describe_reason(player, trend)}")

    drops = rank_drop_candidates(players, mine, trending)
    if drops:
        print()
        print("WEAKEST BENCH (drop candidates, worst first)")
        for score, pid, player in drops[:5]:
            print(f"     {sc.player_label(players, pid):<38} "
                  f"{score:>6.1f}  {describe_reason(player, trending.get(pid, 0))}")

    if available and drops:
        best_score, best_pid, _, best_trend = available[0]
        worst_score, worst_pid, _ = drops[0]
        print()
        if best_score > worst_score * 1.15:
            gain = best_score - worst_score
            print("SUGGESTED MOVE (for your review — nothing is submitted):")
            print(f"  ADD   {sc.player_label(players, best_pid)}")
            print(f"  DROP  {sc.player_label(players, worst_pid)}")
            print(f"  Score gain {gain:.1f} ({worst_score:.1f} -> {best_score:.1f})")
            if budget:
                # Rough FAAB guide: hot adds cost real money, marginal ones don't.
                pct = 1 if best_trend < 1000 else 3 if best_trend < 10000 else 8
                print(f"  Suggested FAAB bid: ~{pct}% "
                      f"({max(1, (budget - used) * pct // 100)} of "
                      f"{budget - used} remaining)")
        else:
            print("NO CLEAR UPGRADE: the best available player does not beat your")
            print("weakest bench spot by enough to be worth a transaction.")


def main():
    args = [a for a in sys.argv[1:]]
    if not args or args[0].startswith("--"):
        print(__doc__.strip().split("HOW PLAYERS")[0].strip())
        return 1
    username = args[0].strip().lstrip("@")

    top_n = 12
    league_filter = None
    if "--top" in args:
        try:
            top_n = int(args[args.index("--top") + 1])
        except (IndexError, ValueError):
            print("--top needs a number, e.g. --top 15")
            return 1
    if "--league" in args:
        try:
            league_filter = args[args.index("--league") + 1].lower()
        except IndexError:
            print("--league needs a league name, e.g. --league LEHG")
            return 1

    try:
        state = sc.current_state()
        season = state.get("season")
        week = state.get("week") or 1
        print(f"NFL {season}, week {week}")

        user = sc.resolve_user(username)
        user_id = user["user_id"]
        leagues = sc.user_leagues(user_id, season)
        if league_filter:
            leagues = [l for l in leagues
                       if league_filter in (l.get("name") or "").lower()]
            if not leagues:
                print(f"No league matching {league_filter!r}.")
                return 1

        print("Loading player database...")
        players = sc.all_players()
        print("Loading trending pickups...")
        trending = trending_adds()
        print(f"{len(players):,} players, {len(trending):,} trending.")

        for league in leagues:
            analyze_league(league, user_id, players, trending, top_n)

        print()
        print("=" * 72)
        print("Recommendations only. Nothing was submitted to Sleeper.")
        print("Rankings blend Sleeper rank + pickup momentum + depth chart —")
        print("no projections, so sanity-check anything before acting on it.")
        print("=" * 72)
        return 0
    except sc.SleeperError as e:
        print(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
