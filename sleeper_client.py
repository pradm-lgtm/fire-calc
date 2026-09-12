#!/usr/bin/env python3
"""
Sleeper read client (step 2 of the fantasy agent).

Sleeper's API is public and unauthenticated, so this needs no credentials —
unlike Yahoo, whose API is now gated behind a reviewed application and is
read-only even once granted (see yahoo_auth_check.py).

Run it to confirm the read path and dump your league state:

    python3 sleeper_client.py <your-sleeper-username>

It resolves your user, lists your NFL leagues for the current season, and
for each one prints your roster with real player names, the league's
scoring settings, and this week's matchup.

The league/roster endpoints are small, but /players/nfl is a ~5 MB blob of
every NFL player. It changes rarely, so it is cached on disk for a day
rather than refetched per run.

Stdlib only. Requires Python 3.8+.
"""

import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = "https://api.sleeper.app/v1"
PLAYERS_CACHE_MAX_AGE = 24 * 60 * 60  # seconds


def cache_dir():
    """Somewhere writable to keep the player database.

    Next to the code on a laptop. On a serverless host the deployment is
    read-only, so it goes to the temp directory instead, which survives
    between requests on a warm instance and is most of them.
    """
    override = os.environ.get("FANTASY_CACHE")
    if override:
        return Path(override)
    here = Path(__file__).resolve().parent
    if os.access(here, os.W_OK):
        return here / ".cache"
    return Path(tempfile.gettempdir()) / "fantasy-cache"


def players_cache():
    return cache_dir() / "players_nfl.json"

# Sleeper returns roster slots in this order; BN/TAXI/IR are bench-ish.
STARTING_SLOTS = {"QB", "RB", "WR", "TE", "FLEX", "SUPER_FLEX", "WRRB_FLEX",
                  "REC_FLEX", "K", "DEF", "DL", "LB", "DB", "IDP_FLEX"}


class SleeperError(RuntimeError):
    pass


def get(path):
    """GET an API path, returning parsed JSON (None for Sleeper's null 404s)."""
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise SleeperError(f"HTTP {e.code} for {url}\n{detail}") from None
    except urllib.error.URLError as e:
        raise SleeperError(f"Could not reach {url}: {e.reason}") from None
    if not body.strip():
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise SleeperError(f"Non-JSON response from {url}:\n{body[:400]}") from None


# ---------------------------------------------------------------- endpoints

def current_state():
    return get("/state/nfl") or {}


def resolve_user(username):
    user = get(f"/user/{urllib.parse.quote(username, safe='')}")
    if not user or not user.get("user_id"):
        raise SleeperError(
            f"No Sleeper user named {username!r}. Use your username (the one in "
            "your profile URL), not your display name or email."
        )
    return user


def user_leagues(user_id, season):
    return get(f"/user/{user_id}/leagues/nfl/{season}") or []


def league_rosters(league_id):
    return get(f"/league/{league_id}/rosters") or []


def league_users(league_id):
    return get(f"/league/{league_id}/users") or []


def league_matchups(league_id, week):
    return get(f"/league/{league_id}/matchups/{week}") or []


def all_players(refresh=False):
    """The full NFL player map, cached on disk (it is ~5 MB and rarely changes)."""
    cache = players_cache()
    if not refresh and cache.exists():
        age = time.time() - cache.stat().st_mtime
        if age < PLAYERS_CACHE_MAX_AGE:
            try:
                return json.loads(cache.read_text())
            except json.JSONDecodeError:
                pass  # corrupt cache: fall through and refetch
    players = get("/players/nfl") or {}
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(players))
    except OSError:
        # Nowhere to write is slower, not broken: the next call refetches.
        pass
    return players


# ---------------------------------------------------------------- shaping

def player_label(players, player_id):
    """'Josh Allen (BUF QB)' — falls back to the raw id for unknown players."""
    p = players.get(str(player_id))
    if not p:
        return str(player_id)
    name = p.get("full_name") or " ".join(
        filter(None, [p.get("first_name"), p.get("last_name")])
    ) or p.get("last_name") or str(player_id)
    team = p.get("team") or "FA"
    pos = p.get("position") or "?"
    label = f"{name} ({team} {pos})"
    status = p.get("injury_status")
    if status and status.lower() not in ("healthy", "active"):
        label += f" [{status}]"
    return label


def my_roster(rosters, user_id):
    for r in rosters:
        if str(r.get("owner_id")) == str(user_id):
            return r
    return None


def split_roster(roster):
    """Return (starters, bench) player-id lists for one roster."""
    starters = [p for p in (roster.get("starters") or []) if p and p != "0"]
    everyone = [p for p in (roster.get("players") or []) if p and p != "0"]
    bench = [p for p in everyone if p not in starters]
    return starters, bench


def find_matchup(matchups, roster_id):
    """Return (my_entry, opponent_entry) for a roster in a week's matchups."""
    mine = next(
        (m for m in matchups if str(m.get("roster_id")) == str(roster_id)), None
    )
    if not mine:
        return None, None
    opponent = next(
        (
            m
            for m in matchups
            if m.get("matchup_id") == mine.get("matchup_id")
            and str(m.get("roster_id")) != str(roster_id)
        ),
        None,
    )
    return mine, opponent


def scoring_summary(league):
    """The handful of scoring settings that actually change waiver decisions."""
    s = league.get("scoring_settings") or {}
    ppr = s.get("rec", 0)
    kind = {0: "Standard", 0.5: "Half-PPR", 1: "Full PPR"}.get(ppr, f"{ppr} pts/rec")
    bits = [kind]
    if s.get("bonus_rec_te"):
        bits.append(f"TE premium +{s['bonus_rec_te']}")
    if s.get("pass_td"):
        bits.append(f"pass TD {s['pass_td']}")
    return ", ".join(bits)


# ---------------------------------------------------------------- report

def describe_league(league, user_id, week, players):
    print()
    print("=" * 70)
    print(f"{league.get('name', '?')}  ({league.get('league_id')})")
    print("=" * 70)
    settings = league.get("settings") or {}
    print(f"Teams: {settings.get('num_teams', '?')}   "
          f"Scoring: {scoring_summary(league)}   "
          f"Status: {league.get('status', '?')}")
    waiver_type = {0: "rolling waivers", 1: "reverse standings", 2: "FAAB"}.get(
        settings.get("waiver_type"), settings.get("waiver_type")
    )
    print(f"Waivers: {waiver_type}", end="")
    if settings.get("waiver_budget"):
        print(f"   FAAB budget: {settings['waiver_budget']}")
    else:
        print()

    rosters = league_rosters(league["league_id"])
    mine = my_roster(rosters, user_id)
    if not mine:
        print("Could not find your roster in this league.")
        return

    wins = (mine.get("settings") or {}).get("wins", 0)
    losses = (mine.get("settings") or {}).get("losses", 0)
    print(f"Your record: {wins}-{losses}")
    if (mine.get("settings") or {}).get("waiver_budget_used") is not None:
        used = mine["settings"]["waiver_budget_used"]
        total = settings.get("waiver_budget")
        print(f"FAAB used: {used}" + (f" of {total}" if total else ""))

    starters, bench = split_roster(mine)
    slots = league.get("roster_positions") or []
    starting_slots = [s for s in slots if s in STARTING_SLOTS]

    print()
    print(f"STARTERS (week {week}):")
    for i, pid in enumerate(starters):
        slot = starting_slots[i] if i < len(starting_slots) else "?"
        print(f"  {slot:<11} {player_label(players, pid)}")
    print()
    print(f"BENCH ({len(bench)}):")
    for pid in bench:
        print(f"  {'':<11} {player_label(players, pid)}")

    try:
        matchups = league_matchups(league["league_id"], week)
    except SleeperError as e:
        print(f"\n(Could not load week {week} matchup: {e})")
        return
    me, opp = find_matchup(matchups, mine.get("roster_id"))
    if me:
        print()
        line = f"WEEK {week} MATCHUP: you {me.get('points', 0)}"
        if opp:
            owner = next(
                (
                    r
                    for r in rosters
                    if str(r.get("roster_id")) == str(opp.get("roster_id"))
                ),
                {},
            )
            names = {u["user_id"]: u for u in league_users(league["league_id"])}
            who = names.get(str(owner.get("owner_id")), {})
            label = who.get("display_name") or f"roster {opp.get('roster_id')}"
            line += f"  vs  {label} {opp.get('points', 0)}"
        else:
            line += "  (no opponent — bye or unscheduled)"
        print(line)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 sleeper_client.py <your-sleeper-username>")
        print()
        print("Your username is the one in your Sleeper profile URL — not your")
        print("display name and not your email.")
        return 1
    username = sys.argv[1].strip().lstrip("@")

    try:
        state = current_state()
        season = state.get("season") or str(time.gmtime().tm_year)
        week = state.get("week") or 1
        print(f"NFL {season}, week {week} ({state.get('season_type', '?')})")

        user = resolve_user(username)
        user_id = user["user_id"]
        print(f"User: {user.get('display_name') or username}  (id {user_id})")

        leagues = user_leagues(user_id, season)
        if not leagues:
            print(f"No NFL leagues found for {username!r} in {season}.")
            return 1
        print(f"Found {len(leagues)} league(s) for {season}.")

        print("Loading player database (cached for a day, first run is slow)...")
        players = all_players()
        print(f"Player database: {len(players)} players.")

        for league in leagues:
            describe_league(league, user_id, week, players)

        print()
        print("=" * 70)
        print("Sleeper read path OK — leagues, rosters, and matchups all load.")
        print("=" * 70)
        return 0
    except SleeperError as e:
        print(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
