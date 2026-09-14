#!/usr/bin/env python3
"""Trade offers worth sending, per league.

A trade happens when both sides think they got better, so that is what this
looks for: packages where your best starting lineup improves and theirs does
too. Two teams with opposite surpluses can both gain, and finding those pairs
is arithmetic over rosters you can already see.

Everything here is a suggestion. Nothing is ever sent; Sleeper has no write
API and a trade is not a one-dollar waiver claim.

    python3 trades.py YOUR_SLEEPER_USERNAME
    python3 trades.py YOUR_SLEEPER_USERNAME --league LEHG

WHAT THE NUMBERS MEAN, AND WHAT THEY DO NOT
Values come from FantasyCalc, formed from trades people actually made in
their calculator. They know your league's scoring but nothing about your
roster - that a receiver is your third is a fact they cannot see - so they
weigh a deal, they do not judge it. The lineup gain below is the part that
knows your roster, and it is the number to read first.
"""

import itertools
import sys

import localenv
import sleeper_client as sc
import trade_values as tv

# Slots a player may fill, mirroring the start/sit rules.
SLOT_ELIGIBILITY = {
    "QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"},
    "K": {"K"}, "DEF": {"DEF"},
    "FLEX": {"RB", "WR", "TE"}, "WRRB_FLEX": {"RB", "WR"},
    "REC_FLEX": {"WR", "TE"}, "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
}
BENCH = {"BN", "IR", "TAXI"}

# How lopsided a package may be before nobody would look at it. A trade
# calculator's own tolerance is roughly this wide.
FAIRNESS = 0.25

# Bounds on the search. Every extra player considered multiplies the pairs.
MAX_SEND = 8
MAX_RECEIVE = 8
TOP_OFFERS = 4


def starting_slots(league):
    return [s for s in (league.get("roster_positions") or []) if s not in BENCH]


def best_lineup(player_ids, players, values, slots):
    """{slot: player_id} for the best legal lineup you could field.

    Filled most-constrained slot first: a quarterback slot has one kind of
    answer and a flex has three, so letting flex pick first would strand the
    quarterback slot empty and undervalue the roster.
    """
    pool = {pid: values.get(pid, {}).get("value", 0.0) for pid in player_ids}
    positions = {pid: (players.get(pid) or {}).get("position")
                 for pid in player_ids}
    used, filled = set(), {}
    for i, slot in sorted(enumerate(slots),
                          key=lambda s: len(SLOT_ELIGIBILITY.get(s[1], ()))):
        allowed = SLOT_ELIGIBILITY.get(slot)
        if not allowed:
            continue
        best, best_value = None, -1.0
        for pid in player_ids:
            if pid in used or positions.get(pid) not in allowed:
                continue
            if pool[pid] > best_value:
                best, best_value = pid, pool[pid]
        if best:
            used.add(best)
        filled[i] = best
    return filled


def free_agents(values, taken):
    """{position: value of the best player nobody has}.

    A slot you empty is not a slot you leave empty. Trading your only
    quarterback costs you the difference between him and whoever is on the
    wire, not his whole value, and that difference is often worth paying.
    """
    best = {}
    for pid, entry in values.items():
        if pid in taken:
            continue
        pos = entry.get("position")
        if pos and entry.get("value", 0) > best.get(pos, 0):
            best[pos] = entry["value"]
    return best


def lineup_value(player_ids, players, values, slots, free=None):
    """The best lineup this roster could field, filling gaps off the wire."""
    free = free or {}
    filled = best_lineup(player_ids, players, values, slots)
    total = 0.0
    for i, slot in enumerate(slots):
        allowed = SLOT_ELIGIBILITY.get(slot)
        if not allowed:
            continue
        pid = filled.get(i)
        if pid:
            total += values.get(pid, {}).get("value", 0.0)
        else:
            total += max((free.get(p, 0.0) for p in allowed), default=0.0)
    return total


def value_of(ids, values):
    return sum(values.get(pid, {}).get("value", 0.0) for pid in ids)


def fairness(give, get, values, gaining_side_spots, replacement):
    """How lopsided the package is, and which way.

    Sending more players than you receive hands the other side a roster
    spot, and the spot is worth whatever they can put in it. A calculator
    counts that; ignoring it makes every two-for-one read as worse for them
    than it is.
    """
    mine = value_of(give, values)
    theirs = value_of(get, values)
    if gaining_side_spots > 0:
        theirs += gaining_side_spots * replacement
    return mine, theirs


def team_label(users, rosters, roster_id):
    by_user = {}
    for u in users or []:
        by_user[u.get("user_id")] = ((u.get("metadata") or {}).get("team_name")
                                     or u.get("display_name") or "Someone")
    for r in rosters or []:
        if r.get("roster_id") == roster_id:
            return by_user.get(r.get("owner_id"), "Unclaimed")
    return "Unclaimed"


def record(roster):
    s = roster.get("settings") or {}
    return (s.get("wins", 0), s.get("losses", 0), s.get("ties", 0))


def tradeable(roster, players, values, limit):
    """Players worth naming in a package, most valuable first.

    Anyone with no value at all is left out: a package nobody can price is
    not an offer.
    """
    ids = [str(p) for p in (roster.get("players") or []) if p and p != "0"]
    ids = [pid for pid in ids if values.get(pid, {}).get("value")]
    ids.sort(key=lambda pid: values[pid]["value"], reverse=True)
    return ids[:limit]


def _protected(players, pid, protect):
    """Names on the never-drop list, matched the way that list is written."""
    if not protect:
        return False
    player = players.get(pid) or {}
    full = (player.get("full_name") or " ".join(filter(None, [
        player.get("first_name"), player.get("last_name")]))).strip().lower()
    return bool(full) and full in protect


def offers(league, mine, theirs, players, values, protect=(), free=None):
    """Every package where both starting lineups come out better."""
    slots = starting_slots(league)
    my_ids = [str(p) for p in (mine.get("players") or []) if p and p != "0"]
    their_ids = [str(p) for p in (theirs.get("players") or []) if p and p != "0"]
    free = free if free is not None else free_agents(
        values, set(my_ids) | set(their_ids))

    my_before = lineup_value(my_ids, players, values, slots, free)
    their_before = lineup_value(their_ids, players, values, slots, free)

    can_send = [pid for pid in tradeable(mine, players, values, MAX_SEND)
                if pid not in protect and not _protected(players, pid, protect)]
    can_get = tradeable(theirs, players, values, MAX_RECEIVE)

    found = []
    packages = ([([a], [b]) for a in can_send for b in can_get]
                + [(list(pair), [b]) for pair in itertools.combinations(can_send, 2)
                   for b in can_get])
    my_lineup_before = best_lineup(my_ids, players, values, slots)
    for give, get in packages:
        my_roster = [p for p in my_ids if p not in give] + get
        their_roster = [p for p in their_ids if p not in get] + give
        my_filled = best_lineup(my_roster, players, values, slots)
        their_filled = best_lineup(their_roster, players, values, slots)
        my_after = lineup_value(my_roster, players, values, slots, free)
        their_after = lineup_value(their_roster, players, values, slots, free)
        my_gain = my_after - my_before
        their_gain = their_after - their_before
        if my_gain <= 0 or their_gain <= 0:
            continue

        spots = max(0, len(give) - len(get))
        position = (values.get(get[0], {}).get("position") or "RB")
        sent, received = fairness(give, get, values, spots,
                                  free.get(position, 0.0))
        if max(sent, received) <= 0:
            continue
        tilt = (received - sent) / max(sent, received)
        if abs(tilt) > FAIRNESS:
            continue

        # What actually changes in your starting eleven, so the cost of
        # sending a starter is visible rather than buried in one number.
        changes = []
        for i, slot in enumerate(slots):
            was, now = my_lineup_before.get(i), my_filled.get(i)
            if was != now:
                changes.append({"slot": slot, "out": was, "in": now})

        found.append({
            "give": give, "get": get, "changes": changes,
            "my_gain": round(my_gain), "their_gain": round(their_gain),
            "sent_value": round(sent), "received_value": round(received),
            "tilt": round(tilt * 100),
            "spots": spots,
        })
    # Best for you first, then by how obviously good it is for them, which
    # is what decides whether the offer gets accepted.
    found.sort(key=lambda o: (o["my_gain"], o["their_gain"]), reverse=True)

    # One offer per player you send. Three variations on trading the same
    # quarterback read as three ideas and are one.
    seen, unique = set(), []
    for offer in found:
        key = tuple(sorted(offer["give"]))
        if any(p in seen for p in key):
            continue
        seen.update(key)
        unique.append(offer)
    return unique[:TOP_OFFERS]


def league_offers(league, user_id, players, values, protect=()):
    rosters = sc.league_rosters(league["league_id"])
    mine = sc.my_roster(rosters, user_id)
    if not mine:
        return None
    users = sc.league_users(league["league_id"])
    # Who is actually free in this league, not merely off these two rosters.
    taken = {str(p) for r in rosters for p in (r.get("players") or []) if p}
    free = free_agents(values, taken)
    out = []
    for other in rosters:
        if other.get("roster_id") == mine.get("roster_id"):
            continue
        for offer in offers(league, mine, other, players, values, protect,
                            free):
            offer["with"] = team_label(users, rosters, other.get("roster_id"))
            offer["their_record"] = record(other)
            out.append(offer)
    out.sort(key=lambda o: (o["my_gain"], o["their_gain"]), reverse=True)
    return {"league_id": str(league["league_id"]),
            "league_name": league.get("name"),
            "my_record": record(mine),
            "offers": out[:TOP_OFFERS * 2]}


def board(username, league_filter=None, protect=()):
    state = sc.current_state()
    season = state.get("season")
    values, source = tv.fetch()
    if not values:
        raise RuntimeError("no trade values could be read")
    user = sc.resolve_user(username)
    players = sc.all_players()

    leagues = sc.user_leagues(user["user_id"], season)
    if league_filter:
        leagues = [l for l in leagues
                   if league_filter.lower() in (l.get("name") or "").lower()]
    out = []
    for league in leagues:
        got = league_offers(league, user["user_id"], players, values, protect)
        if got and got["offers"]:
            out.append(got)
    return {"season": season, "source": source, "values": values,
            "leagues": out}


def short(players, pid):
    return sc.player_label(players, pid).split(" [")[0] if pid else "nobody"


def lineup_changes(offer, players):
    """"QB: Lawrence out, Williams in" for every slot that moves.

    The cost of sending a starter is the man who replaces him, and one
    number for the whole roster hides that entirely.
    """
    return [f"{c['slot']}: {short(players, c['in'])} in"
            + (f", {short(players, c['out'])} out" if c["out"] else "")
            for c in offer["changes"]]


def describe(offer, players, values):
    """One sentence on whether they would take it."""
    # Stated from their side, because that is the question: an offer is
    # only worth sending if the other manager sees a reason to accept.
    lean = ("about even by value" if abs(offer["tilt"]) < 6 else
            f"{abs(offer['tilt'])}% in their favour by value" if offer["tilt"] > 0
            else f"{abs(offer['tilt'])}% in your favour by value")
    spare = (f", and frees them {offer['spots']} roster spot"
             f"{'s' if offer['spots'] > 1 else ''}" if offer["spots"] else "")
    return f"It is {lean}{spare}."


def main():
    localenv.load()
    args = sys.argv[1:]
    if not args or args[0].startswith("--"):
        print("usage: python3 trades.py YOUR_SLEEPER_USERNAME [--league NAME]")
        return 1
    league_filter = None
    if "--league" in args:
        try:
            league_filter = args[args.index("--league") + 1]
        except IndexError:
            print("--league needs a name")
            return 1

    import waiver_analyzer as wa
    # The list of players you will not part with applies here too, and more
    # so: a waiver drop costs a roster spot, a trade hands him to a rival.
    protect = wa.never_drop_names()
    got = board(args[0], league_filter, protect=protect)
    players = sc.all_players()
    print(f"Values from {got['source']}.")
    for league in got["leagues"]:
        print()
        print("=" * 72)
        w, l, t = league["my_record"]
        print(f"{league['league_name']}   (you are {w}-{l}"
              f"{f'-{t}' if t else ''})")
        print("=" * 72)
        for offer in league["offers"]:
            give = ", ".join(sc.player_label(players, p).split(" [")[0]
                             for p in offer["give"])
            get = ", ".join(sc.player_label(players, p).split(" [")[0]
                            for p in offer["get"])
            ow, ol, ot = offer["their_record"]
            print()
            print(f"  To {offer['with']} ({ow}-{ol}{f'-{ot}' if ot else ''})")
            print(f"    Send    {give}")
            print(f"    Get     {get}")
            for change in lineup_changes(offer, players):
                print(f"    Lineup  {change}")
            print(f"    Value   you +{offer['my_gain']:,}, "
                  f"them +{offer['their_gain']:,}")
            print(f"    {describe(offer, players, got['values'])}")
    if not got["leagues"]:
        print("\nNo package makes both sides better right now.")
    print()
    print("=" * 72)
    print("Suggestions only. Nothing is sent anywhere.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
