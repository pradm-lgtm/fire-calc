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


def side(entry, names, players, week, slots):
    """One team in a matchup: its score, its lineup, and what is still to come."""
    if not entry:
        return None
    starters = [str(p) for p in (entry.get("starters") or [])]
    points = entry.get("starters_points") or []
    lineup, waiting, projected = [], [], 0.0
    for i, pid in enumerate(starters):
        player = players.get(pid) or {}
        team = (player.get("team") or "").upper()
        # Unknown kickoff counts as played, so a missing schedule understates
        # what is left rather than inventing points that may never arrive.
        to_play = bool(week["kickoffs"]) and not nfl_week.started(
            week["kickoffs"], team)
        scored = round(float(points[i]), 1) if i < len(points) else 0.0
        projection = week["points"].get(pid)
        # What he has already scored, or what he is expected to. Adding a
        # projection to a finished game would count the same points twice.
        projected += projection if (to_play and projection is not None) else scored
        if to_play:
            waiting.append(player.get("position") or "?")
        lineup.append({
            "player_id": pid if pid and pid != "0" else None,
            "name": (sc.player_label(players, pid).split(" [")[0]
                     if pid and pid != "0" else "Empty"),
            "pos": player.get("position"),
            "slot": slots[i] if i < len(slots) else "",
            "points": scored,
            "projection": projection,
            "matchup": week["games"].get(team),
            "to_play": to_play,
        })
    return {"roster_id": entry.get("roster_id"),
            "name": names.get(entry.get("roster_id"), "Unclaimed"),
            "points": round(float(entry.get("points") or 0), 1),
            "projected": round(projected, 1),
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

    slots = [s for s in (league.get("roster_positions") or [])
             if s not in ("BN", "IR", "TAXI")]
    us = side(ours, names, players, week, slots)
    them = side(theirs, names, players, week, slots)

    # Paired by slot, the way both apps show it: the choice you made in a
    # slot only means anything next to the one they made in the same slot.
    rows = []
    for i in range(max(len(us["lineup"]), len(them["lineup"]) if them else 0)):
        mine = us["lineup"][i] if i < len(us["lineup"]) else None
        yours = (them["lineup"][i] if them and i < len(them["lineup"]) else None)
        rows.append({"slot": (mine or yours or {}).get("slot", ""),
                     "mine": mine, "theirs": yours})
    return {"league_id": str(league["league_id"]),
            "league_name": league.get("name"),
            "us": us, "them": them, "rows": rows,
            "margin": round(us["points"] - them["points"], 1) if them else None,
            "projected_margin": (round(us["projected"] - them["projected"], 1)
                                 if them else None)}


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
            left = (f"  {len(team['to_play'])} to play "
                    f"({', '.join(team['to_play'])})" if team["to_play"] else "")
            print(f"  {team['name'][:30]:<32}{team['points']:>7.1f}"
                  f"{team['projected']:>9.1f} proj{left}")
        if b["margin"] is not None:
            print(f"  {'ahead by' if b['margin'] >= 0 else 'behind by':>32}"
                  f"{abs(b['margin']):>7.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
