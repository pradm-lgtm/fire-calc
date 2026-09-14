#!/usr/bin/env python3
"""Player trade values, for weighing one side of a deal against the other.

A trade calculator turns a pile of players into one number. Reading those
numbers from an API is arithmetic; driving the calculator's web form would
be scraping, and this checks which one is available.

Values are somebody's opinion, formed from trades made in their tool. They
know nothing about your roster - that a receiver is your third is a fact
they cannot see - so they weigh a deal, they do not judge it.

    python3 trade_values.py
"""

import json
import sys
import urllib.error
import urllib.request

TIMEOUT = 20

# Half PPR, one quarterback, redraft: the shape of both leagues here. The
# wrong settings give confidently wrong numbers rather than no numbers.
BASE = "https://api.fantasycalc.com/values/current"

# What the value list has to be asked for. Both leagues here are redraft,
# but one is ten teams and one is twelve, and a player is worth more in a
# shallower league where the wire is thinner. Asking once and using the
# answer twice prices one of them wrong.
def url_for(teams=12, ppr=0.5, quarterbacks=1):
    return (f"{BASE}?isDynasty=false&numQbs={quarterbacks}"
            f"&numTeams={teams}&ppr={ppr}&limit=800")


def settings_from(league):
    """The value-list settings this league implies.

    Passing touchdowns are the one thing that cannot be asked for: six-point
    passing lifts every quarterback, and the value list has no parameter for
    it. It is reported rather than modelled, because inventing a multiplier
    would be a number nobody could check.
    """
    settings = league.get("settings") or {}
    scoring = league.get("scoring_settings") or {}
    slots = league.get("roster_positions") or []
    reception = scoring.get("rec")
    return {
        "teams": settings.get("num_teams") or len(slots) and 12 or 12,
        "ppr": float(reception) if isinstance(reception, (int, float)) else 0.5,
        "quarterbacks": 2 if any(s in ("SUPER_FLEX", "QB") and s == "SUPER_FLEX"
                                 for s in slots) else 1,
        "pass_td": scoring.get("pass_td"),
    }


def _get(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (personal fantasy tool; single user)",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.HTTPError, urllib.error.URLError,
            json.JSONDecodeError, TimeoutError, OSError) as exc:
        return {"__error__": f"{type(exc).__name__}: {exc}"}


def values_from(rows):
    """{sleeper_id: {name, position, value, rank, pos_rank}}."""
    out = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        player = row.get("player")
        if not isinstance(player, dict):
            continue
        pid = player.get("sleeperId")
        value = row.get("value", row.get("redraftValue"))
        if not pid or not isinstance(value, (int, float)):
            continue
        out[str(pid)] = {
            "name": player.get("name") or "",
            "position": player.get("position") or "",
            "value": float(value),
            "rank": row.get("overallRank"),
            "pos_rank": row.get("positionRank"),
            "trend": row.get("trend30Day"),
        }
    return out


def _rows(data):
    if isinstance(data, list):
        return data
    return data.get("players") if isinstance(data, dict) else None


def fetch(teams=12, ppr=0.5, quarterbacks=1):
    """({sleeper_id: entry}, source name). Empty if nothing answered."""
    values = values_from(_rows(_get(url_for(teams, ppr, quarterbacks))))
    return (values, "FantasyCalc") if len(values) > 50 else ({}, None)


def replacement_value(values, position, rostered):
    """What the best free agent at a position is worth.

    A trade that sends two players for one hands the other side a roster
    spot, and the spot is worth whatever they can put in it. Ignoring that
    makes every two-for-one look worse for them than it is.
    """
    free = [v["value"] for pid, v in values.items()
            if v["position"] == position and pid not in rostered]
    return max(free) if free else 0.0


def main():
    for teams in (10, 12):
        url = url_for(teams=teams)
        print(f"FantasyCalc, {teams} teams: {url}")
        data = _get(url)
        if isinstance(data, dict) and data.get("__error__"):
            print(f"  did not answer: {data['__error__']}")
            print("  -> values would have to come from somewhere else")
            continue
        rows = _rows(data)
        print(f"  rows: {len(rows) if isinstance(rows, list) else 'not a list'}")
        if isinstance(rows, list) and rows:
            print(f"  fields: {', '.join(sorted(rows[0])[:20])}")
            print(f"  one row: {json.dumps(rows[0])[:400]}")
        values = values_from(rows)
        print(f"  joined by Sleeper id: {len(values)}")
        for pid, v in list(values.items())[:6]:
            print(f"    {pid:<8} {v['name']:<24} {v['position']:<4} "
                  f"{v['value']:>8g}  {v['pos_rank']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
