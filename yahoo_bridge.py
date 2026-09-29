#!/usr/bin/env python3
"""
Matching Yahoo players to the Sleeper records the value model runs on.

WHY THIS EXISTS

Everything that decides a waiver claim - projections, ranks, the keep and
add values fixed over the last week - is keyed by Sleeper player id. Yahoo
has its own ids (470.p.40030) and its own spelling of names. Without a
bridge the Yahoo leagues either get a second, weaker model of their own, or
they get nothing, which is what they get today.

So this maps one to the other and reuses the model whole.

WHAT IT REFUSES TO DO

Guess. A name that matches two Sleeper players and cannot be told apart by
team is left unmatched rather than resolved to whichever came first: a
wrong player here means a claim for somebody you did not ask for, and a
missing one only means a quieter card. Same rule the article gazetteer
follows, for the same reason.

Nothing from Yahoo is stored. The agreement forbids it, and the mapping is
cheap enough to rebuild each run.

    python3 yahoo_bridge.py --check 470.l.715420
"""

import argparse
import sys
from collections import defaultdict

import expert_extract as ex

# Yahoo and Sleeper mostly agree on team abbreviations once case is
# ignored. These are the ones they spell differently, and the list is
# deliberately short: anything not here is handled by uppercasing, and
# --check reports what failed so this can grow from evidence.
TEAM_ALIASES = {
    "WSH": "WAS", "JAC": "JAX", "LAR": "LAR", "LA": "LAR",
    "SD": "LAC", "OAK": "LV", "STL": "LAR", "ARZ": "ARI",
    "CLV": "CLE", "BLT": "BAL", "HST": "HOU",
}

DEF = "DEF"


def team_code(team):
    """Yahoo's 'Hou' and Sleeper's 'HOU' are the same team."""
    code = str(team or "").strip().upper()
    return TEAM_ALIASES.get(code, code)


def normal(name):
    """'C. J. Stroud' and 'CJ Stroud' have to land on the same key.

    expert_extract's normaliser strips the dots, which turns the first of
    those into three words and the second into two, so they never meet.
    Initials written with spaces are common enough in Yahoo's spelling
    that this is worth doing here rather than leaving the player unmatched.
    """
    parts = ex.normalize_name(name or "").split()
    out = []
    for part in parts:
        if len(part) == 1 and out and len(out[-1]) <= 2 and out[-1].isalpha():
            out[-1] += part
        else:
            out.append(part)
    return " ".join(out)


def index_sleeper(players):
    """Two lookups: by name and position, and by name alone.

    The first is the one that should hit. The second catches a player
    Yahoo lists at a position Sleeper disagrees about, which happens most
    for men who move between running back and receiver.
    """
    by_name_pos = defaultdict(set)
    by_name = defaultdict(set)
    for pid, player in players.items():
        full = (player.get("full_name") or " ".join(filter(None, [
            player.get("first_name"), player.get("last_name")]))).strip()
        if not full:
            continue
        key = normal(full)
        if not key:
            continue
        pos = (player.get("position") or "").upper()
        by_name_pos[(key, pos)].add(str(pid))
        by_name[key].add(str(pid))
    return {"name_pos": by_name_pos, "name": by_name}


def narrow(pids, players, team):
    """Cut a set of candidates down by which team they play for."""
    if len(pids) <= 1 or not team:
        return pids
    wanted = team_code(team)
    same = {pid for pid in pids
            if team_code((players.get(pid) or {}).get("team")) == wanted}
    return same or pids


def match_defense(yahoo_player, players):
    """Sleeper keys a team defense by the team's own abbreviation.

    Yahoo calls it "San Francisco" and Sleeper calls it "San Francisco
    49ers", so the names do not meet. The team code does, and for a
    defense that is the whole identity.
    """
    code = team_code(yahoo_player.get("team"))
    if not code:
        return None
    if code in players and (players[code].get("position") or "") == DEF:
        return code
    for pid, player in players.items():
        if ((player.get("position") or "").upper() == DEF
                and team_code(player.get("team")) == code):
            return str(pid)
    return None


def match(yahoo_player, idx, players):
    """(sleeper id, why not) for one Yahoo player."""
    name = yahoo_player.get("name") or ""
    pos = (yahoo_player.get("position") or "").upper()
    if pos == DEF:
        found = match_defense(yahoo_player, players)
        return (found, None) if found else (None, "no Sleeper defense for "
                                                  "that team")
    key = normal(name)
    if not key:
        return None, "no usable name"

    for pids in (idx["name_pos"].get((key, pos)), idx["name"].get(key)):
        if not pids:
            continue
        pids = narrow(pids, players, yahoo_player.get("team"))
        if len(pids) == 1:
            return next(iter(pids)), None
        if len(pids) > 1:
            return None, f"{len(pids)} Sleeper players share that name"
    return None, "no Sleeper player of that name"


def bridge(yahoo_players, players):
    """({yahoo key: sleeper id}, [(yahoo player, why not)])."""
    idx = index_sleeper(players)
    found, missed = {}, []
    for who in yahoo_players:
        pid, why = match(who, idx, players)
        if pid:
            found[who.get("key")] = pid
        else:
            missed.append((who, why))
    return found, missed


def report(found, missed, what):
    """How well it did, in the only terms that matter: who is missing.

    A bridge nobody has measured is the sort of thing this project keeps
    getting caught by, so the rate is printed rather than assumed - and
    every miss is named, because a match rate of 96% is not reassuring if
    the 4% is your starting lineup.
    """
    total = len(found) + len(missed)
    if not total:
        print(f"  {what}: nobody to match")
        return
    rate = 100.0 * len(found) / total
    print(f"  {what}: {len(found)} of {total} matched ({rate:.0f}%)")
    for who, why in missed:
        print(f"      {who.get('name')} ({who.get('team')} "
              f"{who.get('position')}) - {why}")


def check(league_key):
    """Match a real roster and a real wire, and say what did not land."""
    import sleeper_client as sc
    import yahoo_client as yc

    players = sc.all_players()
    leagues = {lg["key"]: lg for lg in yc.my_leagues()}
    league = leagues.get(league_key)
    if not league:
        print(f"{league_key} is not one of your leagues.")
        print("python3 yahoo_client.py lists them.")
        return 1
    print(f"{league['name']}  ({league_key})")

    mine = next((t for t in yc.my_teams(league_key)), None)
    if mine:
        squad = yc.roster(mine["key"], league.get("week"))
        report(*bridge(squad, players), what="your roster")
    wire = yc.free_agents(league_key, count=50)
    report(*bridge(wire, players), what="the wire")
    print()
    print(yc.ATTRIBUTION)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", metavar="LEAGUE_KEY",
                    help="match a real roster and wire, and name every miss")
    args = ap.parse_args()
    if not args.check:
        ap.print_help()
        return 1
    return check(args.check)


if __name__ == "__main__":
    sys.exit(main())
