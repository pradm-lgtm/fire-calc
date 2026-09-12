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
import re

_SCRIPT = re.compile(r"<script\b[^>]*>(.*?)</script>", re.S | re.I)
# `var ecrData = {`, `window.__DATA__ = [`, `render({`, `"players":[`.
_ASSIGNMENT = re.compile(r"[=(:,]\s*([\[{])")
_MIN_BLOB = 200
_MAX_CANDIDATES = 400

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


def _script_bodies(html):
    """The contents of every script tag, largest source of embedded data."""
    for match in _SCRIPT.finditer(html):
        body = match.group(1)
        if len(body) >= _MIN_BLOB:
            yield body


def _json_end(text, start):
    """Index just past the JSON value starting at text[start], or None.

    Brace counting has to respect strings, because player names and team
    notes contain braces and escaped quotes; a naive count ends the object
    in the middle of one.
    """
    opener = text[start]
    closer = {"{": "}", "[": "]"}.get(opener)
    if not closer:
        return None
    depth, in_string, escaped = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def _json_candidates(body):
    """Substrings of a script body that might parse as JSON.

    Two shapes cover what ranking sites ship: a bare JSON document (a
    `type="application/json"` tag) and an assignment, `var ecrData = {...};`.
    The assignment is the common one, and it is never the last thing in its
    script tag, so matching up to `</script>` finds nothing.
    """
    stripped = body.strip()
    if stripped[:1] in "{[":
        yield stripped
    found, consumed = 0, 0
    for match in _ASSIGNMENT.finditer(body):
        start = match.start(1)
        # Every object nested inside one already yielded would be matched
        # again by the same pattern; on a 500KB bundle that is thousands of
        # redundant scans of the same characters.
        if start < consumed:
            continue
        stop = _json_end(body, start)
        if not stop:
            continue
        consumed = stop
        if stop - start >= _MIN_BLOB:
            yield body[start:stop]
            found += 1
            if found >= _MAX_CANDIDATES:
                return


def ranked_rows_from_html(html):
    """[(player_name, rank)] found in embedded JSON, best effort."""
    if not html:
        return []
    best = []
    for body in _script_bodies(html):
        for blob in _json_candidates(body):
            try:
                parsed = json.loads(blob)
            except ValueError:
                continue
            rows = []
            _rows_from(parsed, rows)
            if len(rows) > len(best):
                best = rows
    # One name, one rank: these blobs repeat players across tiers, notes and
    # "other positions" tables, and the best rank is the ranked list's own.
    seen = {}
    for name, rank in best:
        key = name.strip().lower()
        if key and (key not in seen or rank < seen[key][1]):
            seen[key] = (name, rank)
    return sorted(seen.values(), key=lambda row: row[1])


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


# ---------------------------------------------------------------- diagnostics

def _diagnose(url):
    """Say what a ranking page actually yields, raw and rendered.

    Ranking pages fail in ways that look identical from the outside - a page
    that serves no data, one whose table is drawn by JavaScript, and one
    whose names do not match the player database all end as "nothing found".
    This separates them.
    """
    import expert_waivers as ew
    import render

    raw = ew.fetch_raw(url) or ""
    print(f"raw HTML: {len(raw):,} chars")
    rows = ranked_rows_from_html(raw)
    print(f"  embedded rows: {len(rows)}")
    for name, rank in rows[:10]:
        print(f"    {rank:>6.1f}  {name}")
    if len(rows) >= 10:
        print("  -> the page ships its rankings; no browser needed")
        return 0

    got = render.fetch_rendered_full(url)
    if not got:
        print("rendered: failed")
        return 1
    print(f"rendered HTML: {len(got['html']):,} chars, "
          f"text {len(got['text']):,} chars")
    rows = ranked_rows_from_html(got["html"])
    print(f"  embedded rows: {len(rows)}")
    for name, rank in rows[:10]:
        print(f"    {rank:>6.1f}  {name}")
    if not rows:
        print("  no embedded data either; first 800 chars of visible text:")
        print("  " + got["text"][:800].replace("\n", "\n  "))
    return 0


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("usage: python3 rankings.py <ranking-page-url>")
        raise SystemExit(1)
    raise SystemExit(_diagnose(sys.argv[1]))
