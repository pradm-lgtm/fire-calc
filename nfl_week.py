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
import re
import sys
import time
from datetime import datetime, timezone
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


# Sleeper publishes the season's schedule with a status on each game, which
# answers the only question the page has - has this team played yet - without
# arithmetic on a clock. A timestamp has to be compared against now, in the
# right zone, and has been wrong three times; a status is just read.
SCHEDULE_SOURCES = [
    "https://api.sleeper.com/schedule/nfl/regular/{season}",
    "https://api.sleeper.app/schedule/nfl/regular/{season}",
]

PRE_GAME = "pre_game"
# Everything Sleeper might call a game that has not kicked off.
NOT_STARTED = {"pre_game", "pregame", "scheduled", "upcoming", "not_started"}


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


def statuses(season, week):
    """{team: status} for one week, from the published schedule.

    A status of pre_game is the whole answer to "yet to play". Anything
    else - in progress, complete, postponed - means his points are on the
    board, however few.
    """
    want = str(week)
    for template in SCHEDULE_SOURCES:
        data = _get(template.format(season=season))
        rows = data if isinstance(data, list) else (
            data.get("games") if isinstance(data, dict) else None)
        if not isinstance(rows, list):
            continue
        out = {}
        for row in rows:
            if not isinstance(row, dict) or str(row.get("week")) != want:
                continue
            status = str(row.get("status") or "").lower()
            for key in ("home", "away"):
                team = team_name(row.get(key))
                if team and status:
                    out[team] = status
        if out:
            return out
    return {}


def byes(season, from_week=1, to_week=18):
    """{team: week} for the week each team has no game.

    Read from the same published schedule: a team absent from a week that
    everyone else plays is on bye. Nothing else says so.
    """
    for template in SCHEDULE_SOURCES:
        data = _get(template.format(season=season))
        rows = data if isinstance(data, list) else (
            data.get("games") if isinstance(data, dict) else None)
        if not isinstance(rows, list):
            continue
        playing, seen = {}, set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                week = int(row.get("week"))
            except (TypeError, ValueError):
                continue
            if not from_week <= week <= to_week:
                continue
            for key in ("home", "away"):
                team = team_name(row.get(key))
                if team:
                    playing.setdefault(week, set()).add(team)
                    seen.add(team)
        if not seen:
            continue
        out = {}
        for week, teams in playing.items():
            # Only a week the rest of the league plays tells you anything.
            if len(teams) < len(seen) - 8:
                continue
            for team in seen - teams:
                out.setdefault(team, week)
        return out
    return {}


def yet_to_play(week, team, points=0.0):
    """Has this team's game not started?

    Three signals, most trustworthy first: a published status, then a
    kickoff time, then whether he has scored. The last one cannot tell a
    player who has not played from one who played and scored nothing, which
    is exactly the case the first one exists to settle.
    """
    team = (team or "").upper()
    status = (week.get("statuses") or {}).get(team)
    if status:
        return status in NOT_STARTED
    if (week.get("kickoffs") or {}).get(team):
        return not started(week["kickoffs"], team)
    return not points


def scoreboard(season, week):
    """{team: {matchup, kickoff}} from the published schedule, best effort.

    The projection records name the opponent but carry no kickoff time, and
    without one there is no way to tell a player who has not played from one
    who played and scored nothing. This is where the clock comes from.
    """
    data = _get(SCOREBOARD.format(season=season, week=week))
    if not isinstance(data, dict):
        return {}
    out = {}
    for event in data.get("events") or []:
        when = _epoch(event.get("date"))
        for competition in event.get("competitions") or []:
            when = _epoch(competition.get("date")) or when
            sides = competition.get("competitors") or []
            if len(sides) != 2:
                continue
            named = [(team_name((s.get("team") or {}).get("abbreviation")),
                      s.get("homeAway") == "home") for s in sides]
            for i, (team, at_home) in enumerate(named):
                other = named[1 - i][0]
                if team and other:
                    out[team] = {"matchup": f"{'vs' if at_home else 'at'} {other}",
                                 "kickoff": when}
    return out


def schedule(season, week):
    """{team: 'vs PHI'} for one week, best effort."""
    return {team: got["matchup"] for team, got in
            scoreboard(season, week).items() if got.get("matchup")}


KICKOFF_KEYS = ("start_time", "kickoff", "game_time", "game_date", "date")


def _epoch(value):
    """Seconds since the epoch, or None if this is not a moment in time.

    A date with no time in it parses happily to midnight, and midnight UTC
    on game day is the evening before in America - so every Sunday game read
    as already started from Saturday night onward, and nobody was ever left
    to play. A day is not a kickoff.
    """
    if isinstance(value, (int, float)):
        # Milliseconds if it is far too large to be seconds.
        return float(value) / 1000 if value > 1e11 else float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    if not _HAS_TIME.search(text):
        return None
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()


_HAS_TIME = re.compile(r"\d{1,2}:\d{2}")


def kickoffs_from(rows):
    """{team: epoch seconds} for when each team's game starts."""
    out = {}
    for row in rows:
        team = team_name(_first(row, ("team", "team_abbr", "player_team")))
        when = _epoch(_first(row, KICKOFF_KEYS))
        if team and when:
            out[team] = when
    return out


# Kickoff times are UTC; the day a game belongs to is its day in the eastern
# United States, where the schedule is written. A fixed five-hour shift is
# enough to decide that, and needs no timezone database: it can only put a
# game on the wrong day if one kicked between midnight and 1am eastern, and
# the earliest kickoff in the season is a London morning.
EASTERN_SHIFT = 5 * 3600
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def game_day(kickoff):
    """'Thu', 'Sun', 'Mon' ... for a kickoff, or None if it is not known."""
    if not kickoff:
        return None
    try:
        when = datetime.fromtimestamp(float(kickoff) - EASTERN_SHIFT,
                                      tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    return DAYS[when.weekday()]


def started(kickoffs, team, now=None):
    """Has this team's game begun? Unknown kickoff means no."""
    when = kickoffs.get((team or "").upper())
    if not when:
        return False
    return (now if now is not None else time.time()) >= when


def _first(row, keys):
    for key in keys:
        if row.get(key) not in (None, ""):
            return row[key]
    return None


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
        if home is None and _first(row, ("home_team",)) is not None:
            home = _first(row, ("home_team",)) == team
        # Naming the opponent without claiming a direction beats claiming
        # the wrong one: every game reading as away is worse than none of
        # them saying.
        out[team] = (against if home is None
                     else f"{'vs' if home else 'at'} {against}")
    return out


def projections(season, week):
    """{player_id: points} for one week, best effort."""
    return points_from(projection_rows(season, week))


def week_context(season, week):
    """Points, opponents and kickoff times, from as few calls as possible.

    The projection records answer reliably and carry the opponent, so they
    go first. They have never carried a kickoff time, and the schedule is
    asked only for what is still missing.
    """
    rows = projection_rows(season, week)
    games = matchups_from(rows)
    kickoffs = kickoffs_from(rows)
    state = statuses(season, week)

    if not (kickoffs or state) or len(games) < 24:
        published = scoreboard(season, week)
        for team, got in published.items():
            if got.get("kickoff") and team not in kickoffs:
                kickoffs[team] = got["kickoff"]
            # A direction read off the schedule beats a bare opponent name.
            if got.get("matchup") and " " in got["matchup"]:
                games[team] = got["matchup"]
            elif team not in games and got.get("matchup"):
                games[team] = got["matchup"]
    return {"points": points_from(rows), "games": games,
            "kickoffs": kickoffs, "statuses": state}


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

    raw = [(_first(r, ("team",)), {k: r.get(k) for k in KICKOFF_KEYS
                                   if r.get(k) is not None})
           for r in rows[:3]]
    print(f"kickoff fields on the first rows: {raw}")

    state = statuses(season, week)
    print(f"game statuses: {len(state)} teams  <- this is the one that matters")
    for team, status in sorted(state.items())[:6]:
        print(f"    {team:<4} {status}")
    if state:
        print("    a team is 'yet to play' only while its game reads pre_game")
    else:
        print("    (none; falling back to kickoff times, then to whether he "
              "has scored)")

    # Both of these are fallbacks for when the statuses above are missing.
    starts = kickoffs_from(rows)
    print(f"fallback, kickoff times in the records: {len(starts)} teams")
    published = scoreboard(season, week)
    print(f"fallback, published schedule: {len(published)} teams")
    return 0


if __name__ == "__main__":
    sys.exit(main())
