#!/usr/bin/env python3
"""This week's NFL games and projected points, for context on a lineup page.

Neither of these decides anything. The start/sit verdict is analyst rankings
against your lineup and nothing else, on purpose. These are here so that when
the page says to consider benching someone you can see who he plays and what
he is expected to score without opening another app.

Both degrade to nothing. A missing opponent or projection leaves that line
blank; it never blocks the verdict, which is the part that matters.

    python3 nfl_week.py 2026 1
"""

import json
import sys
import urllib.error
import urllib.request

TIMEOUT = 15

# Sleeper and ESPN spell three teams differently. Sleeper's spelling wins,
# because every player record in this project comes from Sleeper.
ESPN_TO_SLEEPER = {"WSH": "WAS", "LAR": "LAR", "JAX": "JAX"}

SCOREBOARD = ("https://site.api.espn.com/apis/site/v2/sports/football/nfl/"
              "scoreboard?dates={season}&seasontype=2&week={week}")

PROJECTIONS = ("https://api.sleeper.com/projections/nfl/{season}/{week}"
               "?season_type=regular&position[]=QB&position[]=RB"
               "&position[]=WR&position[]=TE&position[]=K&position[]=DEF"
               "&order_by=ppr")

# Half-PPR first: both leagues here are half, and a full-PPR number shown
# next to a half-PPR ranking would quietly overstate every receiver.
POINT_KEYS = ("pts_half_ppr", "pts_ppr", "pts_std")


def _get(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (personal fantasy tool; single user)",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.HTTPError, urllib.error.URLError,
            json.JSONDecodeError, TimeoutError, OSError):
        return None


def team_name(abbreviation):
    abbreviation = (abbreviation or "").upper()
    return ESPN_TO_SLEEPER.get(abbreviation, abbreviation)


def schedule(season, week):
    """{team: 'vs PHI'} or {team: 'at PHI'} for one week, best effort."""
    data = _get(SCOREBOARD.format(season=season, week=week))
    if not isinstance(data, dict):
        return {}
    out = {}
    for event in data.get("events") or []:
        for competition in event.get("competitions") or []:
            sides = competition.get("competitors") or []
            if len(sides) != 2:
                continue
            named = []
            for side in sides:
                team = (side.get("team") or {}).get("abbreviation")
                named.append((team_name(team), side.get("homeAway") == "home"))
            for i, (team, at_home) in enumerate(named):
                other = named[1 - i][0]
                if team and other:
                    out[team] = f"{'vs' if at_home else 'at'} {other}"
    return out


def projections(season, week):
    """{player_id: points} for one week, best effort."""
    data = _get(PROJECTIONS.format(season=season, week=week))
    rows = data if isinstance(data, list) else (data or {}).get("data")
    if not isinstance(rows, list):
        return {}
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = row.get("player_id")
        stats = row.get("stats")
        if not pid or not isinstance(stats, dict):
            continue
        for key in POINT_KEYS:
            value = stats.get(key)
            if isinstance(value, (int, float)):
                out[str(pid)] = round(float(value), 1)
                break
    return out


def main():
    if len(sys.argv) != 3:
        print("usage: python3 nfl_week.py SEASON WEEK")
        return 1
    season, week = sys.argv[1], sys.argv[2]

    games = schedule(season, week)
    print(f"schedule: {len(games)} teams")
    for team, where in sorted(games.items())[:6]:
        print(f"    {team:<4} {where}")
    if not games:
        print("    (nothing came back; the page will just omit opponents)")

    points = projections(season, week)
    print(f"projections: {len(points)} players")
    for pid, value in list(points.items())[:6]:
        print(f"    {pid:<8} {value}")
    if not points:
        print("    (nothing came back; the page will just omit points)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
