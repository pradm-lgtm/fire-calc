#!/usr/bin/env python3
"""Live matchup scores across every league, on one page.

Checking four leagues means four apps, so this reads each one's current
matchup from Sleeper and puts them side by side. Sleeper updates a roster's
points during games, so this is live for as long as the games are.

Nothing here decides anything; it is a scoreboard.

    python3 scores.py YOUR_SLEEPER_USERNAME
"""

import sys

import localenv
import nfl_week
import sleeper_client as sc


def team_names(users, rosters):
    """{roster_id: team name} preferring what someone actually called it."""
    by_user = {}
    for u in users or []:
        label = ((u.get("metadata") or {}).get("team_name")
                 or u.get("display_name") or "Someone")
        by_user[u.get("user_id")] = label
    out = {}
    for r in rosters or []:
        out[r.get("roster_id")] = by_user.get(r.get("owner_id"), "Unclaimed")
    return out


def side(entry, names, players, week):
    """One team in a matchup: its score and who is still to play."""
    if not entry:
        return None
    starters = [str(p) for p in (entry.get("starters") or []) if p and p != "0"]
    points = entry.get("starters_points") or []
    lineup, waiting = [], 0
    for i, pid in enumerate(starters):
        player = players.get(pid) or {}
        team = (player.get("team") or "").upper()
        # Unknown kickoff counts as played, so a missing schedule understates
        # what is left rather than inventing points that may never arrive.
        to_play = bool(week["kickoffs"]) and not nfl_week.started(
            week["kickoffs"], team)
        waiting += 1 if to_play else 0
        lineup.append({
            "player_id": pid,
            "name": sc.player_label(players, pid).split(" [")[0],
            "pos": player.get("position"),
            "points": round(float(points[i]), 1) if i < len(points) else 0.0,
            "matchup": week["games"].get(team),
            "to_play": to_play,
        })
    return {"roster_id": entry.get("roster_id"),
            "name": names.get(entry.get("roster_id"), "Unclaimed"),
            "points": round(float(entry.get("points") or 0), 1),
            "to_play": waiting, "lineup": lineup}


def league_board(league, user_id, players, week_no, week):
    rosters = sc.league_rosters(league["league_id"])
    mine = sc.my_roster(rosters, user_id)
    if not mine:
        return None
    matchups = sc.league_matchups(league["league_id"], week_no) or []
    names = team_names(sc.league_users(league["league_id"]), rosters)

    my_id = mine.get("roster_id")
    ours = next((m for m in matchups if m.get("roster_id") == my_id), None)
    if not ours:
        return None
    theirs = next((m for m in matchups
                   if m.get("matchup_id") == ours.get("matchup_id")
                   and m.get("roster_id") != my_id), None)

    us, them = side(ours, names, players, week), side(theirs, names, players, week)
    return {"league_id": str(league["league_id"]),
            "league_name": league.get("name"),
            "us": us, "them": them,
            "margin": round(us["points"] - them["points"], 1) if them else None}


def board(username, week_no=None):
    """Every league's current matchup, best effort per league."""
    state = sc.current_state()
    season = state.get("season")
    week_no = week_no or state.get("week") or 1
    user = sc.resolve_user(username)
    players = sc.all_players()
    week = nfl_week.week_context(season, week_no)

    out = []
    for league in sc.user_leagues(user["user_id"], season):
        got = league_board(league, user["user_id"], players, week_no, week)
        if got:
            out.append(got)
    # Closest game first: that is the one worth watching.
    out.sort(key=lambda b: abs(b["margin"]) if b["margin"] is not None else 999)
    return {"season": season, "week": week_no, "leagues": out}


def main():
    localenv.load()
    if len(sys.argv) < 2:
        print("usage: python3 scores.py YOUR_SLEEPER_USERNAME")
        return 1
    got = board(sys.argv[1])
    print(f"NFL {got['season']}, week {got['week']}")
    for b in got["leagues"]:
        print()
        print("=" * 60)
        print(b["league_name"])
        for who in ("us", "them"):
            team = b[who]
            if not team:
                print("  (no opponent this week)")
                continue
            left = f"  {team['to_play']} to play" if team["to_play"] else ""
            print(f"  {team['name'][:30]:<32}{team['points']:>7.1f}{left}")
        if b["margin"] is not None:
            print(f"  {'ahead by' if b['margin'] >= 0 else 'behind by':>32}"
                  f"{abs(b['margin']):>7.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
