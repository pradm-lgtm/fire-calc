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

import sys

import localenv
import nfl_week
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


# What an injury does to a player's trade value. A value list is formed
# from trades made over weeks and lags a fresh injury badly: a receiver who
# will miss a month is still priced as though he plays this Sunday.
INJURY_DISCOUNT = {
    "ir": 0.45, "pup": 0.45, "sus": 0.5, "susp": 0.5, "out": 0.7,
    "doubtful": 0.8, "questionable": 0.95,
}


def discount_for(player):
    status = (player.get("injury_status") or "").strip().lower()
    for key, factor in INJURY_DISCOUNT.items():
        if status.startswith(key):
            return factor
    return 1.0


def apply_injuries(values, players):
    """The same values, marked down for anyone who is hurt.

    Applied to both sides equally, so it does not tilt a deal by itself. It
    changes which players look like a need and which look expendable, which
    is the part that was wrong: a receiver about to miss a month read as
    depth at receiver.
    """
    out = {}
    for pid, entry in values.items():
        factor = discount_for(players.get(pid) or {})
        out[pid] = dict(entry, value=entry["value"] * factor,
                        hurt=factor < 1.0)
    return out


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


def fairness(give, get, values, freed_spots, replacement):
    """What each side is really giving up, and so which way it tilts.

    Sending two players for one leaves you a player short and so a roster
    spot free, and that spot is worth whatever you can put in it off the
    wire. So the credit belongs to the side receiving fewer players - you,
    in a two-for-one - which is also where a trade calculator puts it.
    """
    sent = value_of(give, values)
    received = value_of(get, values)
    if freed_spots > 0:
        received += freed_spots * replacement
    return sent, received


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


def median_league(league):
    """Does this league also score you against the weekly median?

    Sleeper counts that second result into wins and losses itself, so the
    record already includes it. Worth saying which record you are reading.
    """
    return bool((league.get("settings") or {}).get("league_average_match"))


def tradeable(roster, players, values):
    """Every player on the roster anyone can price, most valuable first.

    The whole roster, because that is how trades actually get made: the
    piece that fits is often the ninth-most valuable man on the team, not
    one of the headliners. Only players with no value at all are left out,
    since a package nobody can price is not an offer.
    """
    ids = [str(p) for p in (roster.get("players") or []) if p and p != "0"]
    ids = [pid for pid in ids if values.get(pid, {}).get("value")]
    ids.sort(key=lambda pid: values[pid]["value"], reverse=True)
    return ids


def _protected(players, pid, protect):
    """Names on the never-drop list, matched the way that list is written."""
    if not protect:
        return False
    player = players.get(pid) or {}
    full = (player.get("full_name") or " ".join(filter(None, [
        player.get("first_name"), player.get("last_name")]))).strip().lower()
    return bool(full) and full in protect


def shape(league, roster, players, values, byes=None, week=1):
    """What this roster is long and short of, and what is coming.

    Everything the offers are built from, said out loud. Reading a paragraph
    and disagreeing with it is a faster way to find a broken assumption than
    reading twenty packages and disagreeing with four of them.
    """
    byes = byes or {}
    slots = starting_slots(league)
    ids = [str(p) for p in (roster.get("players") or []) if p and p != "0"]

    need = {}
    for slot in slots:
        for pos in SLOT_ELIGIBILITY.get(slot, ()):
            need[pos] = need.get(pos, 0) + 1 / len(SLOT_ELIGIBILITY[slot])

    have, hurt, upcoming = {}, [], {}
    for pid in ids:
        player = players.get(pid) or {}
        pos = player.get("position")
        if not pos:
            continue
        have.setdefault(pos, []).append(pid)
        if discount_for(player) < 1.0:
            hurt.append((pid, pos, player.get("injury_status")))
        bye = byes.get((player.get("team") or "").upper())
        if bye and bye >= week:
            upcoming.setdefault(bye, []).append(pos)

    depth = {}
    for pos, wanted in need.items():
        depth[pos] = round(len(have.get(pos, [])) - wanted, 1)

    wins, losses, ties = record(roster)
    settings = roster.get("settings") or {}
    return {
        "record": (wins, losses, ties),
        "median": median_league(league),
        "points_for": settings.get("fpts", 0),
        "points_against": settings.get("fpts_against", 0),
        "depth": depth,
        "hurt": hurt,
        "byes": upcoming,
        "starters_value": lineup_value(ids, players, values, slots),
    }


def in_band(sent, received):
    """Is this close enough to fair that anyone would read it?

    Nobody accepts your best player for their worst, and nobody offers it,
    so there is no reason to price either lineup first.
    """
    if max(sent, received) <= 0:
        return None
    tilt = (received - sent) / max(sent, received)
    return tilt if abs(tilt) <= FAIRNESS else None


def candidates(can_send, can_get, values, free):
    """Packages worth pricing, as (give, get).

    Both lists are sorted by value, so the senders that could balance a
    given target sit together; stopping once a pair has overshot avoids
    building most of the combinations at all. Rebuilding two lineups is the
    expensive part, and whole rosters made twenty times as many pairs.
    """
    def worth(pid):
        return values.get(pid, {}).get("value", 0.0)

    for target in can_get:
        want = worth(target)
        spare = free.get(values.get(target, {}).get("position") or "RB", 0.0)
        for one in can_send:
            if in_band(worth(one), want) is not None:
                yield [one], [target]
        # The band a package has to land in. Both lists run from most
        # valuable to least, so as the second man moves down the list the
        # pair only gets cheaper: too dear means keep looking, too cheap
        # means every pair after it is cheaper still.
        ceiling = (want + spare) / (1 - FAIRNESS)
        floor = (want + spare) * (1 - FAIRNESS)
        for i, first in enumerate(can_send):
            if worth(first) > ceiling:
                continue          # past the band before a second is added
            for second in can_send[i + 1:]:
                pair = worth(first) + worth(second)
                if pair < floor:
                    break
                if in_band(pair, want + spare) is not None:
                    yield [first, second], [target]


def offers(league, mine, theirs, players, values, protect=(), free=None):
    """Every package where both starting lineups come out better."""
    slots = starting_slots(league)
    my_ids = [str(p) for p in (mine.get("players") or []) if p and p != "0"]
    their_ids = [str(p) for p in (theirs.get("players") or []) if p and p != "0"]
    free = free if free is not None else free_agents(
        values, set(my_ids) | set(their_ids))

    my_before = lineup_value(my_ids, players, values, slots, free)
    their_before = lineup_value(their_ids, players, values, slots, free)

    can_send = [pid for pid in tradeable(mine, players, values)
                if pid not in protect and not _protected(players, pid, protect)]
    can_get = tradeable(theirs, players, values)

    found = []
    my_lineup_before = best_lineup(my_ids, players, values, slots)
    for give, get in candidates(can_send, can_get, values, free):
        spots = max(0, len(give) - len(get))
        position = (values.get(get[0], {}).get("position") or "RB")
        sent, received = fairness(give, get, values, spots,
                                  free.get(position, 0.0))
        tilt = in_band(sent, received)
        if tilt is None:
            continue

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

        # What actually changes in your starting eleven, so the cost of
        # sending a starter is visible rather than buried in one number.
        changes = []
        for i, slot in enumerate(slots):
            was, now = my_lineup_before.get(i), my_filled.get(i)
            if was != now:
                changes.append({"slot": slot, "out": was, "in": now})

        found.append({
            "give": give, "get": get, "changes": changes,
            # The raw units are FantasyCalc's own scale, where the best
            # player in the game is about ten thousand. A share of your
            # starting lineup is a number that means something.
            "my_pct": round(100 * my_gain / my_before) if my_before else 0,
            "their_pct": (round(100 * their_gain / their_before)
                          if their_before else 0),
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
    week = state.get("week") or 1
    user = sc.resolve_user(username)
    players = sc.all_players()
    bye_weeks = nfl_week.byes(season)

    leagues = sc.user_leagues(user["user_id"], season)
    if league_filter:
        leagues = [l for l in leagues
                   if league_filter.lower() in (l.get("name") or "").lower()]

    out, source, cache = [], None, {}
    for league in leagues:
        # Each league is priced with its own size and scoring. A player is
        # worth more in a ten-team league than a twelve, and using one list
        # for both prices one of them wrong.
        wanted = tv.settings_from(league)
        key = (wanted["teams"], wanted["ppr"], wanted["quarterbacks"])
        if key not in cache:
            cache[key] = tv.fetch(*key)
        values, source = cache[key]
        if not values:
            continue
        values = apply_injuries(values, players)

        got = league_offers(league, user["user_id"], players, values, protect)
        if not got:
            continue
        rosters = sc.league_rosters(league["league_id"])
        mine = sc.my_roster(rosters, user["user_id"])
        got["shape"] = shape(league, mine, players, values, bye_weeks, week)
        got["summary"] = summary(got["shape"], players, league.get("name"))
        got["settings"] = wanted
        got["values"] = values
        out.append(got)

    if not out and not source:
        raise RuntimeError("no trade values could be read")
    return {"season": season, "week": week, "source": source, "leagues": out}


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


def posture(shape_of_team):
    """Buying, selling, or neither, from the record alone."""
    wins, losses, _ties = shape_of_team["record"]
    played = wins + losses + shape_of_team["record"][2]
    # A record needs results behind it. Reading anything into 0-0, or into
    # one week of a season, is reading noise.
    if played == 0:
        return "unplayed"
    if played < 3 or (shape_of_team.get("median") and played < 6):
        return "early"
    if wins >= losses * 2:
        return "buying"
    if losses >= wins * 2:
        return "selling"
    return "even"


OPENING = {
    "early": "Too early to read the season",
    "buying": "Off to a strong start",
    "selling": "The season is getting away",
    "even": "Middle of the pack so far",
}


def summary(shape_of_team, players, league_name=""):
    """A paragraph about the team, in the terms the offers are built from."""
    wins, losses, ties = shape_of_team["record"]
    record = f"{wins}-{losses}" + (f"-{ties}" if ties else "")
    stance = posture(shape_of_team)

    # Exactly enough is not thin: one quarterback in a one-quarterback
    # league is a normal roster, not a hole.
    thin = sorted(p for p, spare in shape_of_team["depth"].items()
                  if spare <= -0.5)
    deep = sorted(p for p, spare in shape_of_team["depth"].items()
                  if spare >= 1.5)

    if stance == "unplayed":
        said = ["No games have counted yet, so nothing here reads the "
                "season — only the roster."]
    else:
        counted = (", counting the weekly median result"
                   if shape_of_team.get("median") else "")
        said = [f"{OPENING[stance]} at {record}{counted}."]
    if thin:
        said.append(f"You are thin at {listed(thin)}, which is where an offer "
                    "will try to bring somebody in.")
    if deep:
        said.append(f"You have more {listed(deep)} than you can start, so that "
                    "is what goes out.")
    if not thin and not deep:
        said.append("Your roster is evenly stocked, so there is little to "
                    "trade from and little to trade for.")

    if shape_of_team["hurt"]:
        names = listed([f"{short(players, pid)} ({status})"
                        for pid, _pos, status in shape_of_team["hurt"][:3]])
        said.append(f"{names} marked down for injury, which counts as need at "
                    "that position rather than depth.")

    weeks = sorted(shape_of_team["byes"])[:2]
    if weeks:
        clusters = "; ".join(
            f"week {w}: {listed(sorted(set(shape_of_team['byes'][w])))}"
            for w in weeks)
        said.append(f"Byes ahead — {clusters}.")

    if stance == "buying":
        said.append("Worth paying slightly over the odds for a starter.")
    elif stance == "selling":
        said.append("Worth taking the safer half of a close deal.")
    # Byes and the record describe the roster; they are not inputs to any
    # package. Every offer here is built from lineup value alone.
    return " ".join(said)


def listed(items):
    items = [str(i) for i in items]
    if len(items) <= 1:
        return items[0] if items else ""
    return ", ".join(items[:-1]) + " and " + items[-1]


def describe(offer, players, values):
    """One sentence on whether they would take it."""
    # Stated from their side, because that is the question: an offer is
    # only worth sending if the other manager sees a reason to accept.
    # tilt is (received - sent), so a positive number is value coming your
    # way. Both halves of this sentence said the opposite.
    lean = ("about even by value" if abs(offer["tilt"]) < 6 else
            f"{abs(offer['tilt'])}% in your favour by value" if offer["tilt"] > 0
            else f"{abs(offer['tilt'])}% in their favour by value")
    spare = (f", and leaves you {offer['spots']} roster spot"
             f"{'s' if offer['spots'] > 1 else ''} free"
             if offer["spots"] else "")
    return f"It is {lean}{spare}."


def written_out(got, players):
    """The board with every offer already turned into text.

    Stored this way so the page needs neither the player database nor a
    value list to render it: reading a run becomes one query.
    """
    leagues = []
    for league in got["leagues"]:
        offers = []
        for offer in league["offers"]:
            wins, losses, ties = offer["their_record"]
            offers.append({
                "with": offer["with"],
                "their_record": f"{wins}-{losses}" + (f"-{ties}" if ties else ""),
                "send": [short(players, p) for p in offer["give"]],
                "get": [short(players, p) for p in offer["get"]],
                "changes": lineup_changes(offer, players),
                "my_pct": offer["my_pct"], "their_pct": offer["their_pct"],
                "verdict": describe(offer, players, {}),
            })
        leagues.append({"league_id": league["league_id"],
                        "league_name": league["league_name"],
                        "summary": league["summary"],
                        "settings": league["settings"], "offers": offers})
    return leagues


def run_and_store(username, db_path=None, protect=()):
    """Work the offers out and save them. Returns the run id."""
    import store as st

    got = board(username, protect=protect)
    players = sc.all_players()
    conn = st.connect(db_path) if db_path else st.connect()
    try:
        return st.write_trade_run(conn, got["season"], got["week"],
                                  got["source"], written_out(got, players))
    finally:
        conn.close()


def main():
    localenv.load()
    args = sys.argv[1:]
    if not args or args[0].startswith("--"):
        print("usage: python3 trades.py YOUR_SLEEPER_USERNAME [--league NAME]")
        return 1
    if "--save" in args:
        import cloud_client
        import waiver_analyzer as wa
        protect = wa.never_drop_names()
        if cloud_client.configured():
            got = board(args[0], protect=protect)
            result = cloud_client.push_trades(
                got["season"], got["week"], got["source"],
                written_out(got, sc.all_players()))
            print(f"Sent offers for {result.get('written', 0)} league(s) to "
                  f"{cloud_client.base_url()}.")
        else:
            run = run_and_store(args[0], protect=protect)
            print(f"Saved trade run {run}.")
        return 0

    league_filter = None
    if "--league" in args:
        try:
            league_filter = args[args.index("--league") + 1]
        except IndexError:
            print("--league needs a name")
            return 1

    import textwrap
    import waiver_analyzer as wa
    # The list of players you will not part with applies here too, and more
    # so: a waiver drop costs a roster spot, a trade hands him to a rival.
    protect = wa.never_drop_names()
    got = board(args[0], league_filter, protect=protect)
    players = sc.all_players()
    print(f"Values from {got['source']}, week {got['week']}.")
    for league in got["leagues"]:
        settings = league["settings"]
        print()
        print("=" * 72)
        print(league["league_name"])
        print("=" * 72)
        print(textwrap.fill(league["summary"], 72))
        print()
        note = (f"Priced as {settings['teams']} teams, {settings['ppr']} PPR, "
                f"{settings['quarterbacks']} QB.")
        if settings.get("pass_td") not in (None, 4):
            note += (f" This league gives {settings['pass_td']} for a passing "
                     "touchdown, which lifts quarterbacks; the value list has "
                     "no setting for it, so read QB prices as low here.")
        print(textwrap.fill(note, 72))
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
            print(f"    Lineup  you +{offer['my_pct']}%, "
                  f"them +{offer['their_pct']}%")
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
