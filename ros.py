#!/usr/bin/env python3
"""
Rest-of-season rankings, which are the right question for a drop.

WHY THIS EXISTS

Everything the model knew about a player's worth was either a weekly
projection or Sleeper's search_rank. Neither answers "should I still own
him": a projection is about one Sunday, and search_rank is how often a
name gets looked up. So the model kept offering good players as the drop
while obviously finished ones sat on the roster.

Analysts publish exactly the number wanted here, every week, and the
machinery to read their pages already exists for the start/sit check. A
rest-of-season rank IS a keep value - somebody whose job is watching
football saying who is worth holding from here - so it leads, and the
projections fill in around it.

Nothing is stored. It is refetched each run like everything else.
"""

import math

import lineup
import rankings as rk

# Half-PPR to match the leagues. The overall list is the one that matters:
# a drop compares a receiver against a running back, which a per-position
# list cannot do.
SOURCES = [{
    "name": "FantasyPros rest of season",
    "url": "https://www.fantasypros.com/nfl/rankings/ros-half-point-ppr-overall.php",
    "overall": True,
    "overall_only": True,
}]

# Roughly how many players a rest-of-season list ranks. Beyond it, a man is
# not being held by anyone for the rest of the year.
FLOOR = 300


def fetch(players, verbose=False):
    """{player_id: rest-of-season rank}. Empty if the page will not read."""
    try:
        per_source = lineup.gather_rankings(
            [entry["url"] for entry in SOURCES], players, verbose=verbose)
    except Exception:
        return {}
    merged = rk.merge(per_source).get(rk.OVERALL) or {}
    return {pid: got["rank"] for pid, got in merged.items()
            if isinstance(got.get("rank"), (int, float))}


def value(rank):
    """A rank as a 0-100 worth, on the same scale as everything else.

    Log-shaped for the same reason Sleeper's rank is: the gap between the
    third best player available and the tenth matters far more than the gap
    between the two hundredth and the two hundred and seventh.
    """
    if not isinstance(rank, (int, float)) or rank <= 0:
        return None
    if rank > FLOOR:
        return 0.0
    return 100.0 * (1.0 - math.log10(rank) / math.log10(FLOOR))


def worth(ranks, pid):
    """The keep worth of one player.

    A man missing from a three-hundred-deep list is not missing data - he
    is somebody no analyst would hold for the rest of the year, and that
    is the most useful thing the list says about him. Returning None for
    him let him keep a score built on a weekly projection and a search
    rank, so the obviously finished players on a roster were the ones the
    model never offered.

    None only when there are no rankings at all, which is the real
    unknown: the page would not load.
    """
    if not ranks:
        return None
    return value(ranks.get(str(pid))) or 0.0


def unranked(ranks, roster_ids):
    """Who on your roster nobody is holding for the rest of the season.

    The other half of the complaint that prompted this: a league full of
    obviously finished players, none of them being offered. A man missing
    from a three-hundred-deep list is not a judgement call.
    """
    return [str(pid) for pid in roster_ids if str(pid) not in (ranks or {})]
