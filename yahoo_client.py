#!/usr/bin/env python3
"""
Read-only access to your own Yahoo fantasy leagues.

WHAT THE AGREEMENT ALLOWS, AND WHAT THAT MEANS FOR THIS FILE

Yahoo's API Access and Use Agreement grants Personal Use only, read-only,
for your own leagues and teams. Three of its terms shape the code rather
than sitting in a comment somewhere:

  Nothing is stored. "Developer shall not store, cache or index the Yahoo
  Fantasy Information" - so every function here fetches live and returns
  plain dictionaries, and no caller may write what comes back into the
  database. That is the one rule to keep in mind when extending this: the
  Sleeper half of this project persists everything, and the Yahoo half must
  persist nothing. Decisions you make ARE yours to keep; the league state
  those decisions were made against is not.

  Attribution travels with the data. Any page showing it has to carry
  "Fantasy data provided by Yahoo Fantasy", linked to Yahoo Fantasy, in its
  footer. ATTRIBUTION and ATTRIBUTION_URL below are what the page uses.

  Your team and your opponent, never the whole league. The agreement bars
  compiling complete statistics for all players in a fantasy league, so
  matchup() returns two teams and free_agents() returns the wire - never a
  sweep of every roster.

Rate limits are the developer's responsibility under the agreement, so every
request retries with exponential backoff and gives up rather than hammering.

No dependencies beyond the standard library, and every fetch path is
testable offline by stubbing `_get`.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import localenv

API_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"

ATTRIBUTION = "Fantasy data provided by Yahoo Fantasy"
ATTRIBUTION_URL = "https://football.fantasysports.yahoo.com/"

# Yahoo asks for the game key rather than the year. "nfl" means the current
# season, which is what this ever wants.
GAME = "nfl"

RETRIES = 4
BACKOFF = 1.5


class YahooError(RuntimeError):
    pass


def configured():
    localenv.load()
    return bool(os.environ.get("YAHOO_CLIENT_ID")
                and os.environ.get("YAHOO_REFRESH_TOKEN"))


_access = {"token": None, "at": 0.0}


def _refresh():
    """Trade the refresh token for an access token. Returns the token."""
    import base64
    cid = os.environ.get("YAHOO_CLIENT_ID", "")
    secret = os.environ.get("YAHOO_CLIENT_SECRET", "")
    refresh = os.environ.get("YAHOO_REFRESH_TOKEN", "")
    if not (cid and secret and refresh):
        raise YahooError("YAHOO_CLIENT_ID, YAHOO_CLIENT_SECRET and "
                         "YAHOO_REFRESH_TOKEN must all be set")
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "redirect_uri": os.environ.get("YAHOO_REDIRECT_URI",
                                       "https://localhost:8080/"),
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    req.add_header("Authorization", f"Basic {basic}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise YahooError(f"could not refresh the access token: {detail}")
    except Exception as exc:
        raise YahooError(f"could not reach Yahoo: {exc}")
    token = data.get("access_token")
    if not token:
        raise YahooError("Yahoo returned no access token")
    _access["token"] = token
    # Yahoo's tokens last an hour; treat them as shorter so a long run does
    # not fail on the boundary.
    _access["at"] = time.time() + min(int(data.get("expires_in", 3600)), 3000)
    return token


def token():
    localenv.load()
    if _access["token"] and time.time() < _access["at"]:
        return _access["token"]
    return _refresh()


def _get(path):
    """One GET against the fantasy API. Returns parsed JSON.

    Separated from everything above it so the whole module can be exercised
    without a network or a credential.
    """
    url = f"{API_BASE}{path}"
    url += ("&" if "?" in url else "?") + "format=json"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token()}")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def get(path):
    """A GET that respects the rate limits rather than fighting them.

    The agreement makes backing off the developer's job, so a 429 or a
    server error waits and tries again, and anything else is reported as
    itself. Four attempts, then give up: a tool that keeps hammering a
    limited API is the behaviour the limit exists to stop.
    """
    wait = 1.0
    last = None
    for attempt in range(RETRIES):
        try:
            return _get(path)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code == 401 and attempt == 0:
                _access["token"] = None  # expired mid-run; get a fresh one
                continue
            if exc.code not in (429, 500, 502, 503, 504):
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:200]
                except Exception:
                    pass
                raise YahooError(f"HTTP {exc.code} from Yahoo: {detail}")
        except Exception as exc:
            last = exc
        if attempt < RETRIES - 1:
            time.sleep(wait)
            wait *= BACKOFF
    raise YahooError(f"Yahoo did not answer after {RETRIES} attempts: {last}")


# ------------------------------------------------------------------ JSON
# Yahoo's fantasy JSON is a collection of numbered keys with a count beside
# them, and every object is a list of single-key fragments. These two turn
# that into lists and dicts so nothing above has to know about it.

def items(node):
    """The members of a Yahoo numbered collection, in order."""
    if not isinstance(node, dict):
        return []
    out = []
    for i in range(int(node.get("count") or 0) or len(node)):
        member = node.get(str(i))
        if member is None:
            continue
        out.append(member)
    return out


def fields(fragment):
    """Flatten a Yahoo object's list-of-fragments into one dict of scalars."""
    out = {}

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, (dict, list)):
                    walk(value)
                else:
                    out.setdefault(key, value)
        elif isinstance(node, list):
            for member in node:
                walk(member)

    walk(fragment)
    return out


def _content(payload):
    return (payload or {}).get("fantasy_content") or {}


# ------------------------------------------------------------- resources
# Everything below returns plain dictionaries built fresh from a live
# request. None of it is written anywhere, by design - see the module
# docstring.

def my_leagues():
    """Your NFL leagues this season: key, name, size, scoring, current week."""
    payload = get(f"/users;use_login=1/games;game_keys={GAME}/leagues")
    users = _content(payload).get("users") or {}
    out = []
    for user in items(users):
        for game in items((fields_of(user, "games") or {})):
            for league in items((fields_of(game, "leagues") or {})):
                got = fields(league.get("league") if isinstance(league, dict)
                             else league)
                if got.get("league_key"):
                    out.append({
                        "key": got.get("league_key"),
                        "name": got.get("name"),
                        "teams": as_int(got.get("num_teams")),
                        "scoring": got.get("scoring_type"),
                        "week": as_int(got.get("current_week")),
                        "url": got.get("url"),
                    })
    return out


def fields_of(node, key):
    """The sub-object stored under `key` somewhere in a Yahoo fragment."""
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for value in node.values():
            found = fields_of(value, key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for member in node:
            found = fields_of(member, key)
            if found is not None:
                return found
    return None


def as_int(value, fallback=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def my_teams():
    """Your team in each league: team key, name, and the league it is in."""
    payload = get(f"/users;use_login=1/games;game_keys={GAME}/teams")
    out = []
    for user in items(_content(payload).get("users") or {}):
        for game in items(fields_of(user, "games") or {}):
            for team in items(fields_of(game, "teams") or {}):
                got = fields(team.get("team") if isinstance(team, dict)
                             else team)
                key = got.get("team_key")
                if key:
                    out.append({
                        "key": key,
                        "name": got.get("name"),
                        "league": key.rsplit(".t.", 1)[0],
                    })
    return out


def player_rows(fragment):
    """Players out of any Yahoo collection that holds them."""
    out = []
    for entry in items(fields_of(fragment, "players") or {}):
        node = entry.get("player") if isinstance(entry, dict) else entry
        got = fields(node)
        if not got.get("player_key"):
            continue
        out.append({
            "key": got.get("player_key"),
            "name": got.get("full") or got.get("name"),
            "team": got.get("editorial_team_abbr"),
            "position": got.get("primary_position"),
            "slot": got.get("position"),
            "status": got.get("status_full") or got.get("status") or "",
            "points": got.get("total"),
        })
    return out


def roster(team_key, week=None):
    """One team's roster for a week, with the slot each player is in."""
    path = f"/team/{team_key}/roster"
    if week:
        path += f";week={int(week)}"
    path += "/players"
    return player_rows(_content(get(path)))


def matchup(league_key, week=None):
    """Your matchup this week: your team and your opponent, nothing else.

    Two teams on purpose. The agreement bars compiling complete statistics
    for every player in a fantasy league, so there is no function here that
    sweeps all the rosters, and this is not the place to add one.
    """
    path = f"/league/{league_key}/scoreboard"
    if week:
        path += f";week={int(week)}"
    payload = _content(get(path))
    found = []
    for pairing in items(fields_of(payload, "matchups") or {}):
        node = pairing.get("matchup") if isinstance(pairing, dict) else pairing
        teams = []
        for entry in items(fields_of(node, "teams") or {}):
            team = entry.get("team") if isinstance(entry, dict) else entry
            got = fields(team)
            teams.append({
                "key": got.get("team_key"),
                "name": got.get("name"),
                "points": got.get("total"),
                "projected": got.get("projected_points") or got.get("total"),
            })
        if teams:
            found.append(teams)
    return found


def free_agents(league_key, count=50, position=None):
    """The waiver wire and free agents, most relevant first."""
    path = (f"/league/{league_key}/players;status=A;sort=AR"
            f";count={int(count)}")
    if position:
        path += f";position={position}"
    return player_rows(_content(get(path)))


def settings(league_key):
    """What the league's rules are, for pricing a claim against them."""
    got = fields(fields_of(_content(get(f"/league/{league_key}/settings")),
                           "settings") or {})
    return {
        "waiver_type": got.get("waiver_type"),
        "uses_faab": str(got.get("uses_faab") or "").lower() in ("1", "true"),
        "faab_budget": as_int(got.get("faab_balance")),
        "trade_deadline": got.get("trade_end_date"),
        "playoff_start": as_int(got.get("playoff_start_week")),
    }


# ------------------------------------------------------------------ CLI

def summary():
    """Print what your Yahoo leagues look like through this client.

    The first thing worth running once the tokens exist: it proves the
    credentials, the parsing and the read-only grant all work together,
    without writing a thing.
    """
    leagues = my_leagues()
    if not leagues:
        print("No NFL leagues came back. If that is wrong, the token may be")
        print("for the wrong Yahoo account, or the season key has moved on.")
        return 1
    teams = {t["league"]: t for t in my_teams()}
    for league in leagues:
        mine = teams.get(league["key"])
        print(f"\n{league['name']}  ({league['teams']}-team, "
              f"{league['scoring']}, week {league['week']})")
        print(f"  league key  {league['key']}")
        if mine:
            print(f"  your team   {mine['name']}  [{mine['key']}]")
        try:
            rules = settings(league["key"])
            money = (f"FAAB, {rules['faab_budget']} budget"
                     if rules["uses_faab"] else
                     f"waiver type {rules['waiver_type']}")
            print(f"  waivers     {money}")
        except YahooError as exc:
            print(f"  waivers     could not read settings: {exc}")
        if mine:
            try:
                squad = roster(mine["key"], league["week"])
                print(f"  roster      {len(squad)} players, e.g. "
                      + ", ".join(p["name"] for p in squad[:3]))
            except YahooError as exc:
                print(f"  roster      could not read it: {exc}")
    print(f"\n{ATTRIBUTION} ({ATTRIBUTION_URL})")
    return 0


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    ap.add_argument("--leagues", action="store_true",
                    help="list your leagues, teams and rules (the default)")
    ap.add_argument("--wire", metavar="LEAGUE_KEY",
                    help="who is available in that league right now")
    args = ap.parse_args()
    if not configured():
        print("Yahoo is not set up here. YAHOO_CLIENT_ID, YAHOO_CLIENT_SECRET")
        print("and YAHOO_REFRESH_TOKEN need to be in .env - get them with:")
        print("    python3 yahoo_auth_check.py --auth-url")
        return 1
    try:
        if args.wire:
            for player in free_agents(args.wire, count=25):
                hurt = f"  [{player['status']}]" if player["status"] else ""
                print(f"  {player['name']:<24} {player['position'] or '?':<4}"
                      f" {player['team'] or '':<4}{hurt}")
            print(f"\n{ATTRIBUTION} ({ATTRIBUTION_URL})")
            return 0
        return summary()
    except YahooError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
