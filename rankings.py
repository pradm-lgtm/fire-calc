#!/usr/bin/env python3
"""
Positional rankings read off analyst ranking pages.

A rankings page is an ordered list, so the ranking is the order the players
appear in - no interpretation required, and no projections of my own. A
player's position comes from Sleeper's database rather than the page, so a
single page covering every position still yields a rank within each.

Only the first appearance counts: ranking pages repeat names in sidebars,
"biggest risers" boxes and footers, and a later mention is never the rank.

Consensus across sources is the median of a player's positional ranks, which
one source disagreeing wildly cannot drag around.
"""

import json
import statistics
from pathlib import Path

import expert_extract as ex

CONFIG = Path(__file__).resolve().parent / "ranking_sources.json"

# Ranking pages carry navigation and promo text; a player named beyond this
# many mentions deep is almost certainly not part of the ranked list.
MAX_RANKED = 300

# Pseudo-position holding the page's own ordering, used for flex slots where
# players of different positions genuinely compete for one place.
OVERALL = "_overall"


def load_sources():
    if CONFIG.exists():
        return json.loads(CONFIG.read_text())
    return []


def ranks_from_text(text, players, gazetteer):
    """{position: {player_id: rank}} from one ranking page.

    Rank is position in the order of first appearance, counted separately
    per position, so one page of overall rankings yields RB1..RBn, WR1..WRn
    and so on.
    """
    seen, per_position = set(), {}
    overall = {}
    for pid, _start, _end in ex.find_mentions(text, gazetteer):
        if pid in seen:
            continue          # a repeat is never the rank
        seen.add(pid)
        if len(seen) > MAX_RANKED:
            break
        player = players.get(str(pid)) or {}
        pos = player.get("position")
        if not pos:
            continue
        bucket = per_position.setdefault(pos, {})
        bucket[str(pid)] = len(bucket) + 1
        # Kept alongside, because positional ranks cannot be compared across
        # positions: asking whether WR3 beats RB2 for a flex slot is
        # meaningless, and the page's own order is the only thing that can
        # answer it.
        overall[str(pid)] = len(overall) + 1
    per_position[OVERALL] = overall
    return per_position


def merge(per_source):
    """{position: {player_id: {rank, sources, spread}}} across sources."""
    gathered = {}
    for source, by_position in per_source.items():
        for pos, ranks in by_position.items():
            for pid, rank in ranks.items():
                gathered.setdefault(pos, {}).setdefault(pid, []).append(
                    (source, rank))
    out = {}
    for pos, players in gathered.items():
        for pid, pairs in players.items():
            values = [r for _s, r in pairs]
            out.setdefault(pos, {})[pid] = {
                "rank": round(statistics.median(values), 1),
                "sources": [s for s, _r in pairs],
                "spread": max(values) - min(values) if len(values) > 1 else 0,
            }
    return out


def rank_of(consensus, player_id, position):
    entry = (consensus.get(position) or {}).get(str(player_id))
    return entry["rank"] if entry else None


def describe(consensus, player_id, position):
    entry = (consensus.get(position) or {}).get(str(player_id))
    if not entry:
        return "unranked"
    place = int(round(entry["rank"]))
    label = (f"overall {place}" if position == OVERALL
             else f"{position}{place}")
    if len(entry["sources"]) > 1:
        label += f" ({len(entry['sources'])} sources"
        if entry["spread"]:
            label += f", spread {entry['spread']}"
        label += ")"
    return label
