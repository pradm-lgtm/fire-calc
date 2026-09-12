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


def ranks_from_text(text, players, gazetteer, positions=None,
                    overall=False):
    """{position: {player_id: rank}} from one ranking page.

    Rank is position in the order of first appearance, counted separately
    per position, so one page of overall rankings yields RB1..RBn, WR1..WRn
    and so on.
    """
    overall_allowed = overall
    seen, per_position = set(), {}
    overall_order = {}
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
        # A quarterback page is not a ranking of receivers. Names appear all
        # over these pages - navigation, sidebars, links to other rankings -
        # and counting them produces an ordering that means nothing but is
        # indistinguishable from a real one once merged.
        if positions and pos not in positions:
            continue
        bucket = per_position.setdefault(pos, {})
        bucket[str(pid)] = len(bucket) + 1
        # Kept alongside, because positional ranks cannot be compared across
        # positions: asking whether WR3 beats RB2 for a flex slot is
        # meaningless, and the page's own order is the only thing that can
        # answer it.
        overall_order[str(pid)] = len(overall_order) + 1
    # Only a genuinely cross-positional page can order players against each
    # other; a receiver page's order says nothing about running backs.
    if overall_allowed:
        per_position[OVERALL] = overall_order
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


# ---------------------------------------------------------------- embedded data

# Ranking sites ship the table as JSON in a script tag and draw it with
# JavaScript. Reading that is exact; reading the rendered order is an
# inference that silently truncates when a page does not fully load.
_RANK_KEYS = ("rank_ecr", "rank", "ecr", "pos_rank", "rank_ave")
_NAME_KEYS = ("player_name", "name", "player", "full_name")


def _rows_from(value, out):
    """Walk parsed JSON collecting anything shaped like a ranked player."""
    if isinstance(value, dict):
        name = next((value[k] for k in _NAME_KEYS
                     if isinstance(value.get(k), str)), None)
        rank = next((value[k] for k in _RANK_KEYS
                     if isinstance(value.get(k), (int, float))), None)
        if name and rank is not None:
            out.append((str(name), float(rank)))
            return
        for sub in value.values():
            _rows_from(sub, out)
    elif isinstance(value, list):
        for sub in value:
            _rows_from(sub, out)


def ranked_rows_from_html(html):
    """[(player_name, rank)] found in embedded JSON, best effort."""
    if not html:
        return []
    import json as _json
    import re as _re
    best = []
    for match in _re.finditer(r"(\{.{200,}?\})\s*;?\s*</script>", html, _re.S):
        blob = match.group(1)
        try:
            parsed = _json.loads(blob)
        except ValueError:
            continue
        rows = []
        _rows_from(parsed, rows)
        if len(rows) > len(best):
            best = rows
    for match in _re.finditer(r"=\s*(\[\s*\{.{200,}?\}\s*\])\s*;", html, _re.S):
        try:
            parsed = _json.loads(match.group(1))
        except ValueError:
            continue
        rows = []
        _rows_from(parsed, rows)
        if len(rows) > len(best):
            best = rows
    return best


def ranks_from_rows(rows, players, gazetteer, positions=None, overall=False):
    """Turn (name, rank) pairs into the same shape ranks_from_text returns."""
    resolved = []
    for name, rank in rows:
        pid = gazetteer.get(ex.normalize_name(name))
        if pid:
            resolved.append((float(rank), pid))
    resolved.sort()

    seen, per_position, overall_order = set(), {}, {}
    for _rank, pid in resolved:
        if pid in seen:
            continue
        seen.add(pid)
        pos = (players.get(str(pid)) or {}).get("position")
        if not pos or (positions and pos not in positions):
            continue
        bucket = per_position.setdefault(pos, {})
        bucket[str(pid)] = len(bucket) + 1
        overall_order[str(pid)] = len(overall_order) + 1
    if overall:
        per_position[OVERALL] = overall_order
    return per_position
