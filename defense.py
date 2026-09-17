#!/usr/bin/env python3
"""
Streaming a defense: which one to claim this week, and who to drop for him.

A defense is not a player you keep. In a league with a DEF slot the position
is rented weekly, and the question is never "is this defense good" but "is
this defense facing a worse offence than mine is". That makes it a different
problem from the rest of the waiver reasoning, which is about players who are
better or worse than each other regardless of who they play.

So the ranking is projected points for the coming week, which already encodes
the matchup, corroborated by the analysts' weekly defense rankings where
those have been read. Next week's projection is a tiebreaker and nothing
more: two good matchups beat one when the choice is otherwise close, and do
not beat a clearly better week now.

The drop is the defense you are already streaming. A weekly rental swapped
for another rental costs no roster spot and touches nobody else's place on
the team - proposing to cut a skill player to hold two defenses would be a
worse trade than any defense is worth.

Pure functions over data fetched elsewhere, so all of it runs offline.
"""

DEF = "DEF"

# How much better the incoming defense has to project before this is worth
# a claim. Streaming for a tenth of a point is churn with a FAAB cost, and
# projections are not precise enough for the difference to mean anything.
MIN_EDGE = 1.5

# A defense that is barely ahead this week and much better next week is
# still worth taking. This is how much of next week counts when the two are
# close - a tiebreaker, not a second opinion.
NEXT_WEEK_SHARE = 0.35


def wants_defense(league):
    """Does this league start a defense at all?"""
    return DEF in [str(s).upper() for s in (league.get("roster_positions") or [])]


def defenses(players, ids):
    """The team defenses among a list of player ids."""
    out = []
    for pid in ids:
        player = players.get(str(pid)) or {}
        if str(player.get("position") or "").upper() == DEF:
            out.append(str(pid))
    return out


def mine(roster, players):
    """The defense currently on this roster, or None."""
    found = defenses(players, roster.get("players") or [])
    return found[0] if found else None


def available(rosters, players):
    """Every defense nobody in the league holds."""
    taken = set()
    for team in rosters:
        for pid in (team.get("players") or []):
            taken.add(str(pid))
    return [pid for pid in defenses(players, players.keys())
            if pid not in taken]


def outlook(pid, this_week, next_week):
    """(this week's points, next week's points) for one defense."""
    return (float(this_week.get(str(pid)) or 0.0),
            float(next_week.get(str(pid)) or 0.0))


def rank_of(pid, consensus):
    """Where the analysts have this defense this week, or None."""
    if not consensus:
        return None
    row = consensus.get(str(pid))
    if isinstance(row, dict):
        return row.get("rank")
    return row if isinstance(row, int) else None


def merit(pid, this_week, next_week):
    """One number for choosing between defenses, this week weighted most."""
    now, then = outlook(pid, this_week, next_week)
    return now + NEXT_WEEK_SHARE * then


def candidates(free, this_week, next_week, consensus=None):
    """Available defenses, best first.

    Sorted on the blend, so the order is this week's matchup with next week
    breaking ties. Analyst rank is carried for the card rather than folded
    into the number: two scales added together is a number that means
    neither of them.
    """
    rows = []
    for pid in free:
        now, then = outlook(pid, this_week, next_week)
        rows.append({
            "id": pid,
            "points": now,
            "next_points": then,
            "rank": rank_of(pid, consensus),
            "merit": merit(pid, this_week, next_week),
        })
    rows.sort(key=lambda r: (-r["merit"], r["id"]))
    return rows


def why(best, holding, next_opponent=None):
    """The case for the swap, in a sentence a person can check.

    Names the numbers rather than asserting an improvement, because a
    projection gap of two points is an argument and not a fact.
    """
    edge = best["points"] - (holding["points"] if holding else 0.0)
    bits = [f"projected {best['points']:.1f} this week"]
    if holding:
        bits.append(f"{edge:+.1f} on the defense you are streaming "
                    f"({holding['points']:.1f})")
    if best["next_points"]:
        nxt = f"{best['next_points']:.1f} next week"
        if next_opponent:
            # The matchup already reads "at CAR" or "vs PHI", preposition
            # and all, so anything added in front of it says it twice.
            nxt += f" {next_opponent}"
        bits.append(nxt)
    if best["rank"]:
        bits.append(f"analysts have him DST{best['rank']}")
    return ", ".join(bits)


def suggest(league, roster, players, rosters, this_week, next_week,
            consensus=None, matchups=None):
    """(add id, drop id, why) for a defense worth streaming, or None.

    Returns nothing rather than a marginal swap: a league where the best
    available defense is no better than the one held should hear nothing,
    not a claim worth a point of projection and a dollar of budget.
    """
    if not wants_defense(league):
        return None
    free = available(rosters, players)
    if not free:
        return None
    held = mine(roster, players)
    holding = None
    if held:
        now, then = outlook(held, this_week, next_week)
        holding = {"id": held, "points": now, "next_points": then}

    ranked = candidates(free, this_week, next_week, consensus)
    if not ranked:
        return None
    best = ranked[0]
    if holding and best["points"] - holding["points"] < MIN_EDGE:
        return None
    if not holding and best["points"] <= 0:
        return None
    against = (matchups or {}).get(str(best["id"]))
    return best["id"], held, why(best, holding, against)
