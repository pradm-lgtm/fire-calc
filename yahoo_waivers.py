#!/usr/bin/env python3
"""
Waiver proposals for the Yahoo leagues, through the Sleeper-shaped model.

WHY IT IS BUILT THIS WAY

The value model is the part that took a week of being wrong in public to
get right: rest-of-season rankings leading, momentum kept out of keep
decisions, a line under who is worth cutting, a bar at the man a claim
would displace. None of that is about Sleeper. It is keyed by Sleeper
player ids, which is a different thing entirely.

So rather than a second model for Yahoo, this translates: yahoo_bridge
maps Yahoo's players onto Sleeper's records, roster_slots gives the league
the same shape a Sleeper league has, and everything downstream is the code
that already works. A bug fixed for one league is fixed for all four.

WHAT IS DIFFERENT

No bids. Both leagues run waiver priority, so there is no budget to spend
and no number to put in a box; what a claim costs is your place in the
queue. Proposals carry no bid and the card shows none.

Nothing from Yahoo is stored. The agreement forbids it and the proposals
are about players, not about Yahoo's data: an add, a drop, and a reason,
all of them expressed in Sleeper ids by the time they are written down.
"""

import sys

import expert_extract as ex
import expert_waivers as ew
import yahoo_bridge as yb

# Slots that hold somebody without starting him.
BENCHED = {"BN", "IR", "TAXI"}


def as_roster(squad, mapping):
    """A Yahoo roster in the shape sleeper_client.split_roster expects.

    Yahoo says which slot each man is in, which is the same thing
    Sleeper's starters list says less directly - and it says IR outright,
    where Sleeper needs a separate reserve list.
    """
    players, starters, reserve = [], [], []
    for who in squad:
        pid = mapping.get(who.get("key"))
        if not pid:
            continue
        players.append(pid)
        slot = (who.get("slot") or "").upper()
        if slot == "IR":
            reserve.append(pid)
        elif slot not in BENCHED:
            starters.append(pid)
    return {"players": players, "starters": starters, "reserve": reserve,
            "settings": {}}


def as_league(league, slots):
    """A Yahoo league in the shape the model reads."""
    return {
        "league_id": league["key"],
        "name": league.get("name") or league["key"],
        "roster_positions": slots,
        "settings": {"num_teams": league.get("teams") or 10,
                     "waiver_budget": 0},
        "scoring_settings": {},
    }


def gather(league_key=None, verbose=False):
    """[(league, roster, available, players)] for your Yahoo leagues.

    Every fetch is live and nothing is kept, per the agreement. The
    Sleeper player database is the one thing cached, and it is Sleeper's.
    """
    import sleeper_client as sc
    import yahoo_client as yc

    players = sc.all_players()
    out = []
    for league in yc.my_leagues():
        if league_key and league["key"] != league_key:
            continue
        mine = next((t for t in yc.my_teams()
                     if t.get("league") == league["key"]), None)
        if not mine:
            continue
        if verbose:
            print(f"    reading {league.get('name')}")
        squad = yc.roster(mine["key"], league.get("week"))
        wire = yc.free_agents(league["key"], count=75)
        held, missed = yb.bridge(squad, players)
        free, free_missed = yb.bridge(wire, players)
        if verbose:
            yb.report(held, missed, "your roster")
            yb.report(free, free_missed, "the wire")
        out.append({
            "league": as_league(league, yc.roster_slots(league["key"])),
            "team": mine,
            "roster": as_roster(squad, held),
            "available": sorted(set(free.values())),
            "players": players,
            "week": league.get("week") or 1,
        })
    return out


def proposals_for(block, texts, trending, weeks, moves=8, verbose=False):
    """Proposals for one Yahoo league, decided by the Sleeper-shaped model.

    A thin wrapper: build the gazetteer over who is free HERE, read the
    week's articles against it, and hand the rest to run_weekly, which is
    where every rule about need, depth and who is worth cutting lives.
    """
    import run_weekly as rw

    league, mine = block["league"], block["roster"]
    players, available = block["players"], block["available"]
    gaz = ex.build_gazetteer(players, available)
    per_source = {}
    for source, text in texts.items():
        recs = ex.extract_recommendations(text, gaz, source=source,
                                          players=players)
        if recs:
            per_source[source] = recs

    rows, why, turned_down = rw.decide(
        league=league, mine=mine, players=players, available=available,
        consensus=ex.merge_sources(per_source), trending=trending,
        weeks=weeks, week=block["week"], moves=moves,
        remaining=0, rosters=[], cost={}, advice={}, byes={},
        platform="yahoo", verbose=verbose)
    return rows, why, turned_down


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--leagues", action="store_true",
                    help="show what the model sees in each Yahoo league")
    ap.add_argument("--league", metavar="LEAGUE_KEY")
    args = ap.parse_args()

    import yahoo_client as yc
    if not yc.configured():
        print("Yahoo is not set up here. python3 yahoo_client.py --ready")
        return 1
    for block in gather(args.league, verbose=True):
        league = block["league"]
        depth = ew.positional_depth(league, block["roster"], block["players"])
        print()
        print(f"{league['name']}  ({league['league_id']})")
        print(f"  slots    {' '.join(league['roster_positions'])}")
        print(f"  free     {len(block['available'])} players")
        for pos, (have, need, label) in sorted(depth.items()):
            print(f"  {pos:5}    {have} for {need:g} starting ({label})")
    print()
    print(yc.ATTRIBUTION)
    return 0


if __name__ == "__main__":
    sys.exit(main())
