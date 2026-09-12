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


def projection_rows(season, week):
    """The week's projection records, as served. [] if unavailable."""
    data = _get(PROJECTIONS.format(season=season, week=week))
    rows = data if isinstance(data, list) else (data or {}).get("data")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def points_from(rows):
    """{player_id: points}."""
    out = {}
    for row in rows:
        pid, stats = row.get("player_id"), row.get("stats")
        if not pid or not isinstance(stats, dict):
            continue
        for key in POINT_KEYS:
            value = stats.get(key)
            if isinstance(value, (int, float)):
                out[str(pid)] = round(float(value), 1)
                break
    return out


def matchups_from(rows):
    """{team: 'vs PHI'} read off the projection records.

    The projections call already succeeds, and each record says who its
    player faces, so the opponent comes free from a request being made
    anyway rather than from a second service that may or may not answer.
    """
    out = {}
    for row in rows:
        team = team_name(_first(row, ("team", "team_abbr", "player_team")))
        against = team_name(_first(row, ("opponent", "opp", "opponent_team",
                                         "opp_team")))
        if not team or not against or team == against:
            continue
        home = _first(row, ("home", "is_home"))
        if home is None:
            home = _first(row, ("home_team",)) == team
        out[team] = f"{'vs' if home else 'at'} {against}"
    return out


def _first(row, keys):
    for key in keys:
        if row.get(key) not in (None, ""):
            return row[key]
    return None


def projections(season, week):
    """{player_id: points} for one week, best effort."""
    return points_from(projection_rows(season, week))


def week_context(season, week):
    """({player_id: points}, {team: opponent}) from as few calls as possible."""
    rows = projection_rows(season, week)
    games = matchups_from(rows)
    if not games:
        games = schedule(season, week)
    return points_from(rows), games


def main():
    if len(sys.argv) != 3:
        print("usage: python3 nfl_week.py SEASON WEEK")
        return 1
    season, week = sys.argv[1], sys.argv[2]

    rows = projection_rows(season, week)
    print(f"projection records: {len(rows)}")
    if rows:
        # What a record actually contains decides where the opponent comes
        # from, and guessing at it has cost a round trip already.
        print(f"  fields: {', '.join(sorted(rows[0])[:24])}")
        print(f"  one record: {json.dumps(rows[0])[:400]}")

    points = points_from(rows)
    print(f"projected points: {len(points)} players")
    for pid, value in list(points.items())[:4]:
        print(f"    {pid:<8} {value}")

    from_rows = matchups_from(rows)
    print(f"opponents from those records: {len(from_rows)} teams")
    for team, where in sorted(from_rows.items())[:4]:
        print(f"    {team:<4} {where}")

    if not from_rows:
        games = schedule(season, week)
        print(f"opponents from the scoreboard: {len(games)} teams")
        for team, where in sorted(games.items())[:4]:
            print(f"    {team:<4} {where}")
        if not games:
            print("    (neither answered; the page will omit opponents)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
