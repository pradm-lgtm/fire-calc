#!/usr/bin/env python3
"""
Pull structured waiver recommendations out of fantasy-analyst article prose.

Given the text of a "waiver wire pickups of the week" article, find which
players it recommends and how much FAAB it suggests spending on each.

The hard part is that article text is messy: names appear with and without
suffixes ("Brian Robinson Jr."), initials vary ("D.J." vs "DJ"), and any
article mentions far more players than it recommends (opponents, injured
starters, players to drop). Two things keep this honest:

  1. The name gazetteer is built ONLY from players actually available in
     your leagues. That kills nearly all ambiguity — "Josh Allen" is both a
     BUF quarterback and a JAX linebacker, but only one of them is likely
     to be a free agent in your league. It also means an article's mentions
     of rostered stars are ignored automatically.

  2. A mention only becomes a recommendation if a FAAB figure or an
     add-verb appears near it. Being named in an article is not a
     recommendation; being named next to "spend 15%" is.

No network access and no API keys — pure text in, structured data out, so
it is fully testable offline.
"""

import re
import statistics
from collections import defaultdict

# Suffixes and punctuation that vary between sources for the same player.
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# Words that, near a player mention, indicate the article is recommending an
# ADD rather than merely discussing the player.
ADD_VERBS = (
    "add", "adds", "pickup", "pick up", "picking up", "claim", "target",
    "grab", "stash", "waiver", "priority", "bid", "spend", "scoop",
)

# Words indicating the article is telling you to DROP or avoid the player,
# which must not be read as a recommendation to add him.
NEGATIVE_CUES = (
    "drop", "cut", "avoid", "fade", "bench him", "don't bother",
    "do not bother", "not worth", "steer clear", "waive",
)

# How far past a player mention to look for a FAAB figure or cue word.
CONTEXT_WINDOW = 260
# How far back to look, for cues that precede the name ("do not bother with X").
LOOKBACK = 90


def _cue_pattern(cues):
    """Word-boundary matcher. Substring matching is a trap here: 'waive' is
    inside 'waiver', so every article headed 'Waiver Wire' would look like a
    drop recommendation, and 'add' is inside 'additional'."""
    return re.compile(
        r"\b(?:" + "|".join(re.escape(c) for c in cues) + r")\b", re.IGNORECASE
    )


_ADD_RE = _cue_pattern(ADD_VERBS)
_NEG_RE = _cue_pattern(NEGATIVE_CUES)


def normalize_name(name):
    """'D.J. Moore Jr.' -> 'dj moore'  — a stable key for matching."""
    name = name.lower().replace("&amp;", "&")
    name = re.sub(r"[^a-z\s]", "", name)  # drop periods, apostrophes, hyphens
    parts = [p for p in name.split() if p and p not in _SUFFIXES]
    return " ".join(parts)


def build_gazetteer(players, candidate_ids):
    """{normalized name: player_id} for the given candidate players only.

    Names that map to more than one candidate are dropped rather than
    guessed at — a wrong player is worse than a missed one here.
    """
    by_name = defaultdict(set)
    for pid in candidate_ids:
        p = players.get(str(pid))
        if not p:
            continue
        full = p.get("full_name") or " ".join(
            filter(None, [p.get("first_name"), p.get("last_name")])
        )
        if not full:
            continue
        key = normalize_name(full)
        if key:
            by_name[key].add(str(pid))
    return {name: next(iter(ids)) for name, ids in by_name.items() if len(ids) == 1}


def find_mentions(text, gazetteer):
    """[(player_id, start_index, end_index)] for gazetteer names in text.

    Matching is done on a normalized copy of the text while keeping an index
    map back to the original, so offsets stay meaningful for context lookup.
    """
    norm_chars, index_map = [], []
    for i, ch in enumerate(text.lower()):
        if ch.isalpha():
            norm_chars.append(ch)
            index_map.append(i)
        elif ch.isspace():
            if norm_chars and norm_chars[-1] != " ":
                norm_chars.append(" ")
                index_map.append(i)
        # punctuation is dropped, mirroring normalize_name
    norm = "".join(norm_chars)

    found = []
    for name, pid in gazetteer.items():
        start = 0
        while True:
            idx = norm.find(name, start)
            if idx == -1:
                break
            before_ok = idx == 0 or norm[idx - 1] == " "
            after = idx + len(name)
            after_ok = after >= len(norm) or norm[after] == " "
            if before_ok and after_ok:
                s = index_map[idx] if idx < len(index_map) else 0
                e = index_map[after - 1] + 1 if after - 1 < len(index_map) else s
                found.append((pid, s, e))
            start = idx + 1
    return sorted(found, key=lambda row: row[1])


_PCT_RANGE = re.compile(r"(\d{1,3})\s*(?:-|–|—|to)\s*(\d{1,3})\s*%")
_PCT_SINGLE = re.compile(r"(\d{1,3})\s*%")
_DOLLAR_RANGE = re.compile(r"\$\s*(\d{1,3})\s*(?:-|–|—|to)\s*\$?\s*(\d{1,3})")
_DOLLAR_SINGLE = re.compile(r"\$\s*(\d{1,3})")


def extract_faab(context, anchor=0):
    """Suggested FAAB percentage from a snippet, or None.

    Ranges collapse to their midpoint. Dollar figures are read as percentages
    of a $100 budget, the near-universal convention in these articles (and
    both of this user's leagues). When a snippet holds several figures, the
    one nearest `anchor` (the player's name) wins, so a number belonging to
    the previous sentence does not get attributed to this player.
    """
    candidates = []
    for pattern, is_range in ((_PCT_RANGE, True), (_DOLLAR_RANGE, True),
                              (_PCT_SINGLE, False), (_DOLLAR_SINGLE, False)):
        for m in pattern.finditer(context):
            if is_range:
                lo, hi = int(m.group(1)), int(m.group(2))
                if not (lo <= hi <= 100):
                    continue
                value = round((lo + hi) / 2, 1)
            else:
                val = int(m.group(1))
                if not 0 <= val <= 100:
                    continue
                value = float(val)
            # Ordering, most significant first:
            #  1. Figures AFTER the name beat figures before it. These
            #     articles put the bid after the player ("Bigsby — spend
            #     20%"), so a preceding number usually belongs to the
            #     previous player.
            #  2. Then nearest to the name.
            #  3. Then ranges over the bare numbers inside them ("20-25%"
            #     should read as 22.5, not 25).
            candidates.append((0 if m.start() >= anchor else 1,
                               abs(m.start() - anchor),
                               0 if is_range else 1,
                               m.start(), value))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][4]


def _paragraph_bounds(text, index):
    """(start, end) of the blank-line-delimited block containing index.

    Deliberately blank-line based, not line based: article prose wraps, so a
    player's name and his FAAB figure routinely sit on different lines of the
    same paragraph.
    """
    start = text.rfind("\n\n", 0, index)
    start = 0 if start == -1 else start + 2
    end = text.find("\n\n", index)
    return start, len(text) if end == -1 else end


_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def _sentence_bounds(text, index):
    """(start, end) of the sentence containing index.

    Negative cues are judged at sentence scope. "Do not bother with X" is
    about X, but a following sentence about a different player must not
    suppress the recommendation before it.
    """
    start = 0
    for m in _SENTENCE_END.finditer(text, 0, index):
        start = m.end()
    m = _SENTENCE_END.search(text, index)
    end = m.end() if m else len(text)
    # A line break also ends a thought in list-style articles.
    nl = text.find("\n", index)
    if nl != -1:
        end = min(end, nl)
    prev_nl = text.rfind("\n", start, index)
    if prev_nl != -1:
        start = max(start, prev_nl + 1)
    return start, end


def extract_recommendations(text, gazetteer, source=None):
    """{player_id: {faab, context, source}} for players this article recommends.

    A mention counts only if an add-cue or FAAB figure sits near it and no
    negative cue does. When a player is mentioned several times, the mention
    carrying a FAAB figure wins.
    """
    results = {}
    mentions = find_mentions(text, gazetteer)
    for i, (pid, idx, end_idx) in enumerate(mentions):
        # Bound the context to this player's own text. Without this, a FAAB
        # figure or a "drop him" aimed at a neighbouring player gets read as
        # belonging to this one — which silently produces wrong bids and
        # drops real recommendations.
        lo, hi = _paragraph_bounds(text, idx)
        if i > 0:
            lo = max(lo, mentions[i - 1][2])
        if i + 1 < len(mentions):
            hi = min(hi, mentions[i + 1][1])
        lo = max(lo, idx - LOOKBACK)
        hi = min(hi, end_idx + CONTEXT_WINDOW)
        if hi <= lo:
            continue
        context = text[lo:hi]
        anchor = idx - lo

        sent_lo, sent_hi = _sentence_bounds(text, idx)
        if _NEG_RE.search(text[sent_lo:sent_hi]):
            continue
        faab = extract_faab(context, anchor)
        if faab is None and not _ADD_RE.search(context):
            continue

        prior = results.get(pid)
        if prior and prior.get("faab") is not None and faab is None:
            continue  # keep the richer earlier mention
        results[pid] = {
            "faab": faab,
            # Quote the player's own sentence, not the wider matching window —
            # the window deliberately spans neighbours and reads as noise.
            "context": " ".join(text[sent_lo:sent_hi].split())[:240],
            "source": source,
        }
    return results


def merge_sources(per_source):
    """Combine {source: {pid: rec}} into a consensus view per player.

    Returns {pid: {sources, count, faab_values, faab_median, contexts}}.
    Consensus count is the headline number: how many independent analysts
    named this player, which is exactly the signal the user reads articles
    for.
    """
    merged = defaultdict(lambda: {
        "sources": [], "faab_values": [], "contexts": []
    })
    for source, recs in per_source.items():
        for pid, rec in recs.items():
            entry = merged[pid]
            if source not in entry["sources"]:
                entry["sources"].append(source)
            if rec.get("faab") is not None:
                entry["faab_values"].append(rec["faab"])
            if rec.get("context"):
                entry["contexts"].append((source, rec["context"]))
    out = {}
    for pid, entry in merged.items():
        faabs = entry["faab_values"]
        out[pid] = {
            "sources": entry["sources"],
            "count": len(entry["sources"]),
            "faab_values": faabs,
            "faab_median": round(statistics.median(faabs), 1) if faabs else None,
            "contexts": entry["contexts"],
        }
    return out
