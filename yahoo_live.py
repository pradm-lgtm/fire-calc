#!/usr/bin/env python3
"""
Scores and trade ideas for the Yahoo leagues, within what the agreement allows.

WHAT THE AGREEMENT DECIDES HERE

Section 2.c.x bars compiling complete statistics for all the players in a
fantasy league. That is not a footnote for this file, it is its shape:

  Scores read two teams - yours and the one you are playing - because a
  scoreboard is about a matchup. There is no sweep of the other eight.

  Trade ideas are offered against that same opponent only. The Sleeper
  version walks every roster in the league looking for a partner, and the
  equivalent here would be exactly the compiling the agreement forbids.
  So Yahoo gets opponent-of-the-week ideas, which is a narrower thing
  than Sleeper gets, and the narrowness is the point rather than a gap to
  close later.

Everything else is shared: the same valuations, the same lineup
arithmetic, the same fairness test. yahoo_bridge maps the players across
and the existing code does the thinking.

Nothing is stored.
"""

import sys

import yahoo_bridge as yb

BENCHED = {"BN", "TAXI", "NA"}
RESERVE_PREFIX = ("IR", "IL")


def two_teams(league_key, my_team_key, week=None):
    """(my side, their side) from the week's scoreboard, or (mine, None).

    Yahoo gives every pairing in the league; this keeps the one you are
    in. Reading the others would be the sweep the agreement forbids, and
    is not something a scoreboard needs anyway.
    """
    import yahoo_client as yc

    for pairing in yc.matchup(league_key, week):
        keys = [t.get("key") for t in pairing]
        if my_team_key in keys:
            mine = next(t for t in pairing if t.get("key") == my_team_key)
            theirs = next((t for t in pairing
                           if t.get("key") != my_team_key), None)
            return mine, theirs
    return None, None


def lineup_of(team_key, week, players, mapping=None):
    """One team's starters, in slot order, with what each has scored."""
    import yahoo_client as yc

    squad = yc.roster(team_key, week)
    bridge = mapping if mapping is not None else yb.bridge(squad, players)[0]
    out = []
    for who in squad:
        slot = (who.get("slot") or "").upper()
        if slot in BENCHED or slot.startswith(RESERVE_PREFIX):
            continue
        pid = bridge.get(who.get("key"))
        out.append({
            "player_id": pid,
            "name": who.get("name") or "Empty",
            "pos": who.get("position"),
            "slot": yc.SLOT_NAMES.get(slot, slot),
            "points": as_points(who.get("points")),
            "projection": None,
            "matchup": None,
            "to_play": False,
        })
    return out


def as_points(value):
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return 0.0


def side(team, lineup):
    """One team in the shape the scores page already draws."""
    if not team:
        return None
    return {"roster_id": team.get("key"),
            "name": team.get("name") or "Unclaimed",
            "points": as_points(team.get("points")),
            "projected": as_points(team.get("projected")
                                   or team.get("points")),
            "to_play": [], "lineup": lineup}


def boards(week=None, verbose=False):
    """A scoreboard per Yahoo league, shaped like the Sleeper ones."""
    import sleeper_client as sc
    import yahoo_client as yc

    players = sc.all_players()
    out = []
    for league in yc.my_leagues():
        key = league["key"]
        mine = next((t for t in yc.my_teams() if t.get("league") == key), None)
        if not mine:
            continue
        at = week or league.get("week")
        us, them = two_teams(key, mine["key"], at)
        if not us:
            continue
        ours = side(us, lineup_of(mine["key"], at, players))
        theirs = (side(them, lineup_of(them["key"], at, players))
                  if them else None)
        rows = []
        for i in range(max(len(ours["lineup"]),
                           len(theirs["lineup"]) if theirs else 0)):
            a = ours["lineup"][i] if i < len(ours["lineup"]) else None
            b = (theirs["lineup"][i]
                 if theirs and i < len(theirs["lineup"]) else None)
            rows.append({"slot": (a or b or {}).get("slot", ""),
                         "mine": a, "theirs": b})
        out.append({
            "league_id": key,
            "league_name": league.get("name"),
            "us": ours, "them": theirs, "rows": rows,
            "margin": (round(ours["points"] - theirs["points"], 1)
                       if theirs else None),
            "projected_margin": (round(ours["projected"] - theirs["projected"],
                                       1) if theirs else None),
        })
        if verbose:
            print(f"  {league.get('name')}: {ours['points']} v "
                  f"{theirs['points'] if theirs else '-'}")
    return out


def as_roster(squad, mapping):
    """A Yahoo squad as the plain id list the trade code reads."""
    return {"players": [mapping[w["key"]] for w in squad
                        if mapping.get(w.get("key"))],
            "starters": [], "settings": {}}


def trade_ideas(week=None, protect=(), verbose=False):
    """Offers against the team you are playing, and nobody else.

    The Sleeper version walks every roster in the league looking for a
    partner. Doing that here would be compiling complete statistics for
    the league, which the agreement forbids - so this is narrower on
    purpose, and says so rather than quietly looking like the other one.
    """
    import sleeper_client as sc
    import trades
    import yahoo_client as yc
    import yahoo_waivers as yw

    players = sc.all_players()
    out = []
    for league in yc.my_leagues():
        key = league["key"]
        mine = next((t for t in yc.my_teams() if t.get("league") == key), None)
        if not mine:
            continue
        at = week or league.get("week")
        _us, them = two_teams(key, mine["key"], at)
        if not them:
            continue

        slots = yc.roster_slots(key)
        shaped = yw.as_league(league, slots)
        my_squad = yc.roster(mine["key"], at)
        their_squad = yc.roster(them["key"], at)
        my_map = yb.bridge(my_squad, players)[0]
        their_map = yb.bridge(their_squad, players)[0]

        # Priced for this league's own size and scoring, the way the
        # Sleeper side prices each of its own: a player is worth more in
        # a ten-team league than a twelve.
        import trade_values as tv
        wanted = tv.settings_from(shaped)
        values, _source = tv.fetch(wanted["teams"], wanted["ppr"],
                                   wanted["quarterbacks"])
        if not values:
            continue
        values = trades.apply_injuries(values, players)

        # Who is genuinely free here, from the endpoint that answers that
        # - rather than inferred by subtracting every roster in the
        # league from the player list, which is the sweep we are avoiding.
        wire = set(yb.bridge(yc.free_agents(key, count=100),
                             players)[0].values())
        # free_agents() wants who is taken, and asks for the best man left
        # at each position. Taken is everyone priced who is not on the
        # wire - which comes from the endpoint that answers that question,
        # rather than from subtracting every roster in the league, which
        # is the sweep this file exists to avoid.
        found = trades.offers(shaped, as_roster(my_squad, my_map),
                              as_roster(their_squad, their_map),
                              players, values, protect,
                              trades.free_agents(values,
                                                 set(values) - wire))
        for offer in found:
            offer["with"] = them.get("name")
        if verbose:
            print(f"  {league.get('name')}: {len(found)} idea(s) against "
                  f"{them.get('name')}")
        out.append({"league_id": key, "league_name": league.get("name"),
                    "my_record": "", "opponent": them.get("name"),
                    "offers": found[:trades.TOP_OFFERS * 2]})
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scores", action="store_true")
    ap.add_argument("--trades", action="store_true")
    ap.add_argument("--week", type=int, default=None)
    args = ap.parse_args()

    import yahoo_client as yc
    if not yc.configured():
        print("Yahoo is not set up here. python3 yahoo_client.py --ready")
        return 1
    if args.trades:
        for got in trade_ideas(args.week, verbose=True):
            print()
            print(f"{got['league_name']} - against {got['opponent']}")
            for offer in got["offers"][:5]:
                print(f"  {offer}")
    else:
        for got in boards(args.week, verbose=True):
            print()
            print(f"{got['league_name']}: {got['us']['name']} "
                  f"{got['us']['points']} v "
                  f"{(got['them'] or {}).get('points', '-')} "
                  f"{(got['them'] or {}).get('name', '(bye)')}")
    print()
    print(yc.ATTRIBUTION)
    return 0


if __name__ == "__main__":
    sys.exit(main())
