#!/usr/bin/env python3
"""
The order waiver claims are filed in, and what that order means.

A league processes your own claims in the order you gave them, so two claims
that cannot both land are not a mistake. Adding Mayer while dropping Dobbins
and adding Mayer while dropping somebody else are a first choice and a
fallback, and which is which is decided entirely by their order: put the
fallback second and it only lands if the first one fails, put it first and
you get the wrong one.

Two claims clash when they add the same player - you can only have him once -
or drop the same player, since whichever lands first takes him and the other
has nobody left to cut. Anything else is independent and the order between
them changes nothing.

Everything here is pure: rows in, rows out. The ordering can be reasoned
about, and tested, without a league, a browser or a database.
"""


def plain(name):
    """'Dylan Sampson (CLE RB)' -> 'Dylan Sampson'."""
    text = str(name or "")
    if "(" not in text:
        return text
    head, _, tail = text.partition("(")
    keep = head.strip()
    if "[" in tail:  # an injury flag matters to the decision; keep it
        keep += " " + tail[tail.index("["):].strip()
    return keep


def field(row, name, fallback=None):
    """One column, whether the row came from the database or over the wire.

    The submitter reads its queue from the host as JSON, and a dict that is
    missing a column should order the claims as if it were unset rather than
    blow up on the way to the browser.
    """
    try:
        value = row[name]
    except (KeyError, IndexError, TypeError):
        return fallback
    return fallback if value is None else value


def key(row):
    """Submission order within one league.

    Priority is the person's own ordering and beats everything else. Where
    nobody has said - every claim starts at 0 - the dearest bid goes first,
    which is both the claim that matters most and the one a fallback is a
    fallback to.
    """
    return (field(row, "priority", 0), -field(row, "bid", 0),
            field(row, "rank", 0), field(row, "id", 0))


def ordered(rows):
    """One league's claims in the order they will be filed."""
    return sorted(rows, key=key)


def by_league(rows):
    """{league_id: [rows]}, leagues in the order they first appear."""
    groups = {}
    for row in rows:
        groups.setdefault(str(row["league_id"]), []).append(row)
    return groups


def submission_order(rows):
    """Every claim, league by league, each league in its filing order."""
    out = []
    for group in by_league(rows).values():
        out.extend(ordered(group))
    return out


def clash(first, second):
    """Why `second` cannot land once `first` has, or '' if they are free.

    Only the add and the drop can collide. Budget is not a clash: two claims
    that together cost more than you have can both process if the first one
    loses, and the page already says what approving them all would cost.
    """
    if str(first["add_player_id"]) == str(second["add_player_id"]):
        return f"you cannot add {plain(first['add_player_name'])} twice"
    mine = field(first, "drop_player_id")
    theirs = field(second, "drop_player_id")
    if mine and theirs and str(mine) == str(theirs):
        return f"both drop {plain(first['drop_player_name'])}"
    return ""


def blockers(rows):
    """{claim id: (the claim above it, why)} for claims that are fallbacks.

    The nearest one above, not every one: a claim that is third in a chain
    only reaches its turn if the second failed, and saying so once is what
    the order actually promises.
    """
    seq = ordered(rows)
    out = {}
    for i, row in enumerate(seq):
        for earlier in reversed(seq[:i]):
            why = clash(earlier, row)
            if why:
                out[row["id"]] = (earlier, why)
                break
    return out


def chains(rows):
    """True where any claim in this league depends on another one failing."""
    return bool(blockers(rows))


def moved(rows, proposal_id, direction):
    """The ids of one league's claims after moving one of them one place.

    Moving off either end is a no-op rather than a wrap-around: the button
    is hidden there anyway, and a stale page should not be able to send the
    top claim to the bottom.
    """
    seq = [r["id"] for r in ordered(rows)]
    if proposal_id not in seq:
        return seq
    here = seq.index(proposal_id)
    there = here - 1 if direction == "up" else here + 1
    if 0 <= there < len(seq):
        seq[here], seq[there] = seq[there], seq[here]
    return seq
