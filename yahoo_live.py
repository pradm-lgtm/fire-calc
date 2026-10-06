#!/usr/bin/env python3
"""
Scores and trade ideas for the Yahoo leagues, within what the agreement allows.

WHAT THE AGREEMENT DECIDES HERE

Section 2.c.x bars compiling complete statistics for all the players in a
fantasy league. That is not a footnote for this file, it is its shape:

  Scores read two teams - yours and the one you are playing - because a
  scoreboard is about a matchup. There is no sweep of the other eight.

  Trade ideas look at every team, the way the Sleeper half does. That
  clause is about building a statistics product out of Yahoo's data, not
  about a personal tool reading the league you play in - which is the
  Personal Use the agreement grants, and which Yahoo serves a league
  teams endpoint for. An earlier version here offered trades against the
  week's opponent alone, on a stricter reading of the same sentence; it
  was over-cautious and made the Yahoo half quietly worse than the
  Sleeper half for no reason the agreement actually gives.

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


def as_roster(squad, mapping, record=None):
    """A Yahoo squad as the roster dict the trade code reads.

    The record goes in under `settings` because that is where the Sleeper
    half keeps it, and trades.record() is the one reader. A team whose
    record Yahoo did not give gets no settings at all rather than 0-0,
    which trades.posture() would read as a season not yet played - and
    it would be right to, so saying it is better than guessing.
    """
    out = {"players": [mapping[w["key"]] for w in squad
                       if mapping.get(w.get("key"))],
           "starters": [], "settings": {}}
    if record:
        wins, losses, ties = record
        out["settings"] = {"wins": wins, "losses": losses, "ties": ties}
    return out


def byes_for(season):
    """{team: bye week}, or {} if the schedule would not load.

    Byes colour the summary and feed no package, so the same rule as the
    records applies: read them if they read, carry on if they do not.
    """
    import nfl_week
    try:
        return nfl_week.byes(season) if season else {}
    except Exception as exc:
        print(f"  ! bye weeks unread: {type(exc).__name__}: {exc}")
        return {}


def records_in(league_key):
    """Every team's record, or {} if Yahoo would not say.

    Records make the summary read the season; they are not an input to
    any package. So a standings call that fails costs a paragraph, not
    the trade ideas, and this swallows it on purpose.
    """
    import yahoo_client as yc
    try:
        return yc.standings(league_key)
    except Exception as exc:
        print(f"  ! records unread for {league_key}: "
              f"{type(exc).__name__}: {exc}")
        return {}


def trade_ideas(week=None, protect=(), verbose=False):
    """Offers against every team in the league, as the Sleeper half does.

    One request per team for their roster, which is what the league
    teams endpoint is served for.

    A league comes out of here in the same shape trades.league_offers
    gives the Sleeper half, down to the keys on each offer. Everything
    downstream - the stored run, the page, the terminal - reads both
    through the same code, so a league that is missing a key does not
    fail to render, it takes the whole trade run down with it. That is
    not hypothetical: `their_record` was missing here and written_out
    raised KeyError on the first Yahoo offer, which lost the Sleeper
    leagues in the same run.
    """
    import sleeper_client as sc
    import trades
    import yahoo_client as yc
    import yahoo_waivers as yw

    players = sc.all_players()
    season = (sc.current_state() or {}).get("season")
    bye_weeks = byes_for(season)
    out = []
    for league in yc.my_leagues():
        key = league["key"]
        mine = next((t for t in yc.my_teams() if t.get("league") == key), None)
        if not mine:
            continue
        at = week or league.get("week")
        slots = yc.roster_slots(key)
        shaped = yw.as_league(league, slots)
        my_squad = yc.roster(mine["key"], at)
        my_map = yb.bridge(my_squad, players)[0]

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

        # Who is genuinely free here, from the endpoint that answers
        # that, rather than inferred by subtracting every roster in the
        # league from the player list.
        wire = set(yb.bridge(yc.free_agents(key, count=100),
                             players)[0].values())
        free = trades.free_agents(values, set(values) - wire)

        table = records_in(key)
        my_record = table.get(mine["key"])
        my_roster = as_roster(my_squad, my_map, my_record)

        found = []
        for other in yc.league_teams(key):
            if other["key"] == mine["key"]:
                continue
            squad = yc.roster(other["key"], at)
            their_record = table.get(other["key"])
            theirs = as_roster(squad, yb.bridge(squad, players)[0],
                               their_record)
            for offer in trades.offers(shaped, my_roster, theirs, players,
                                       values, protect, free):
                offer["with"] = other.get("name")
                offer["their_record"] = their_record or (0, 0, 0)
                found.append(offer)
        found.sort(key=lambda o: (o["my_gain"], o["their_gain"]),
                   reverse=True)
        # Counted after the cap, because the number in the heading is a
        # promise about what follows it. It said 8 and showed 6.
        kept = found[:trades.TOP_OFFERS * 2]
        if verbose:
            said = f"{len(kept)} idea(s) across the league"
            if len(found) > len(kept):
                said += f", the best of {len(found)}"
            print(f"  {league.get('name')}: {said}")

        mine_shaped = trades.shape(shaped, my_roster, players, values,
                                   bye_weeks, at or 1)
        out.append({"league_id": key, "league_name": league.get("name"),
                    "my_record": my_record or (0, 0, 0),
                    "shape": mine_shaped,
                    "summary": trades.summary(mine_shaped, players,
                                              league.get("name")),
                    "settings": wanted,
                    "values": values,
                    "offers": kept})
    return out


def say_offer(offer, players):
    """One offer in the terms the page uses.

    The gain printed here is the share of your starting lineup, not the
    raw value units. Those run to four figures on FantasyCalc's scale,
    so "you +453, them +1601" reads as a trade that robs you - when what
    it means is that two lineups with different baselines each improved.
    trades.offers computes both and the page shows the percentage; this
    printed the raw number and made every idea look like a mistake.
    """
    import trades
    send = ", ".join(trades.short(players, p)
                     for p in offer["give"]) or "nobody"
    back = ", ".join(trades.short(players, p)
                     for p in offer["get"]) or "nobody"
    wins, losses, ties = offer.get("their_record") or (0, 0, 0)
    record = f"{wins}-{losses}" + (f"-{ties}" if ties else "")
    print(f"  with {offer.get('with') or '?'} ({record})")
    print(f"    send {send}")
    print(f"    get  {back}")
    for line in trades.lineup_changes(offer, players):
        print(f"    {line}")
    print(f"    your lineup +{offer['my_pct']}%, theirs "
          f"+{offer['their_pct']}%. "
          f"{trades.describe(offer, players, {})}")


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
        import sleeper_client as sc
        import trades
        players = sc.all_players()
        for got in trade_ideas(args.week, verbose=True):
            print()
            print(got["league_name"])
            print(f"  {got['summary']}")
            if not got["offers"]:
                print("  nothing worth proposing")
            for offer in got["offers"]:
                say_offer(offer, players)
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
