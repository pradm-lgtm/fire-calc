#!/usr/bin/env python3
"""
Expert-consensus waiver recommendations (step 4 of the fantasy agent).

Reads the waiver-wire articles you already read, works out which of their
recommended players are actually available in each of your leagues, checks
those against your roster's positional gaps, and proposes specific
add/drop pairs with FAAB bids.

    # Easiest on macOS - copy the article text, then pipe the clipboard:
    pbpaste | python3 expert_waivers.py pradm7 --stdin

    # Or save articles as .txt/.html in a folder and read them all:
    python3 expert_waivers.py pradm7 --articles ~/waiver-articles/

    # Or fetch pages directly:
    python3 expert_waivers.py pradm7 --url https://site/waivers --url https://other/wire

    # More suggested moves per league (default 3):
    python3 expert_waivers.py pradm7 --articles ~/waiver-articles/ --moves 5

WHY IT WORKS THIS WAY
The ranking is the analysts', not mine. This tool does the part that is
tedious by hand and that no article can do for you: cross-referencing a
dozen recommended names against who is genuinely free in YOUR leagues,
against YOUR positional needs, and against who on your bench is expendable.
Consensus across sources is the headline signal — a player three analysts
name is a stronger add than one a single analyst likes.

Nothing is ever submitted. Every output is a proposal for you to approve.
"""

import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import expert_extract as ex
import sleeper_client as sc
import waiver_analyzer as wa

FLEX_SLOTS = {"FLEX", "WRRB_FLEX", "REC_FLEX", "SUPER_FLEX", "IDP_FLEX"}
FLEX_ELIGIBLE = {"RB", "WR", "TE"}
NON_STARTING = {"BN", "IR", "TAXI"}


# ---------------------------------------------------------------- sources

def strip_html(html):
    """Crude but adequate tag stripper — article text, not perfect fidelity."""
    html = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&#39;", "'"),
                         ("&rsquo;", "'"), ("&quot;", '"'), ("&mdash;", "—"),
                         ("&ndash;", "–"), ("&lt;", "<"), ("&gt;", ">")):
        html = html.replace(entity, char)
    return re.sub(r"[ \t]{2,}", " ", html)


def load_article_files(directory):
    """{name: text} from every .txt/.html/.md file in a directory."""
    path = Path(directory).expanduser()
    if not path.is_dir():
        raise SystemExit(f"Not a directory: {path}")
    out = {}
    for f in sorted(path.iterdir()):
        if f.suffix.lower() not in (".txt", ".html", ".htm", ".md"):
            continue
        raw = f.read_text(errors="replace")
        out[f.name] = strip_html(raw) if f.suffix.lower() in (".html", ".htm") else raw
    if not out:
        raise SystemExit(
            f"No .txt/.html/.md files in {path}.\n"
            "Save the waiver articles you read into that folder first "
            "(browser: File > Save Page As, or paste the text into a .txt)."
        )
    return out


def fetch_url(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (personal fantasy tool; single user)",
        "Accept": "text/html,application/xhtml+xml",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return strip_html(resp.read().decode("utf-8", "replace"))
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        print(f"  ! could not fetch {url}: {e}")
        return None


# ---------------------------------------------------------------- roster shape

def starting_requirements(league):
    """{position: number of starting slots}, plus a count of flex slots."""
    required, flex = {}, 0
    for slot in (league.get("roster_positions") or []):
        if slot in NON_STARTING:
            continue
        if slot in FLEX_SLOTS:
            flex += 1
        else:
            required[slot] = required.get(slot, 0) + 1
    return required, flex


def roster_by_position(roster, players):
    counts = {}
    for pid in (roster.get("players") or []):
        p = players.get(str(pid))
        if not p:
            continue
        pos = p.get("position")
        if pos:
            counts.setdefault(pos, []).append(str(pid))
    return counts


def positional_depth(league, roster, players):
    """{position: (have, needed, label)} where label is thin/ok/deep.

    Flex slots are spread across RB/WR/TE rather than assigned, which is
    imprecise but enough to tell a position with no backup from one with
    three.
    """
    required, flex = starting_requirements(league)
    have = roster_by_position(roster, players)
    flex_share = flex / len(FLEX_ELIGIBLE) if flex else 0

    depth = {}
    for pos in sorted(set(list(required) + list(have))):
        need = required.get(pos, 0)
        if pos in FLEX_ELIGIBLE:
            need += flex_share
        count = len(have.get(pos, []))
        if need <= 0:
            label = "extra"
        elif count <= need:
            label = "thin"
        elif count >= need + 2:
            label = "deep"
        else:
            label = "ok"
        depth[pos] = (count, round(need, 1), label)
    return depth


def choose_drop(roster, players, trending, depth, protect_ids):
    """Weakest bench player, preferring positions with surplus depth.

    Never proposes dropping from a position labelled thin unless nothing
    else is available — losing your only backup RB to add a WR is a bad
    trade even when the WR scores better.
    """
    _, bench = sc.split_roster(roster)
    ranked = []
    for pid in bench:
        pid = str(pid)
        if pid in protect_ids:
            continue
        p = players.get(pid)
        if not p:
            continue
        score = wa.score_player(p, trending.get(pid, 0))
        label = depth.get(p.get("position"), (0, 0, "ok"))[2]
        penalty = {"thin": 1000, "ok": 100, "deep": 0, "extra": 0}.get(label, 100)
        ranked.append((score + penalty, score, pid, p, label))
    ranked.sort(key=lambda row: row[0])
    return ranked


# ---------------------------------------------------------------- report

def analyze_league(league, user_id, players, trending, per_source_text, max_moves):
    name = league.get("name", "?")
    print()
    print("=" * 74)
    print(f"{name}   ({league.get('league_id')})")
    print("=" * 74)

    rosters = sc.league_rosters(league["league_id"])
    mine = sc.my_roster(rosters, user_id)
    if not mine:
        print("Could not find your roster here; skipping.")
        return

    settings = league.get("settings") or {}
    budget = settings.get("waiver_budget") or 0
    used = (mine.get("settings") or {}).get("waiver_budget_used", 0)
    remaining = budget - used if budget else 0
    print(f"{settings.get('num_teams','?')} teams | {sc.scoring_summary(league)}"
          + (f" | FAAB {remaining} of {budget} left" if budget else ""))

    taken = wa.rostered_player_ids(rosters)
    available_ids = [
        pid for pid, p in players.items()
        if pid not in taken and wa.is_rosterable(p)
    ]
    # Two passes. The wide one (every rosterable NFL player) exists only to
    # explain an empty result: without it, "nothing available" cannot be told
    # apart from "the text parsed to nothing", and those need opposite fixes.
    wide_ids = [pid for pid, p in players.items() if wa.is_rosterable(p)]
    wide_gaz = ex.build_gazetteer(players, wide_ids)
    wide_per_source = {}
    for source, text in per_source_text.items():
        recs = ex.extract_recommendations(text, wide_gaz, source=source)
        if recs:
            wide_per_source[source] = recs
    wide_consensus = ex.merge_sources(wide_per_source)

    # Scoping the gazetteer to free agents is what makes name matching safe.
    gazetteer = ex.build_gazetteer(players, available_ids)
    per_source = {}
    for source, text in per_source_text.items():
        recs = ex.extract_recommendations(text, gazetteer, source=source)
        if recs:
            per_source[source] = recs
    consensus = ex.merge_sources(per_source)

    blocked = [pid for pid in wide_consensus if pid in taken]
    print(f"Parsed {len(wide_consensus)} recommended player(s) from your "
          f"source(s): {len(consensus)} free here, {len(blocked)} already rostered.")

    depth = positional_depth(league, mine, players)
    print("Your depth: " + "  ".join(
        f"{pos} {have}/{need} [{label}]" for pos, (have, need, label) in depth.items()
        if label != "extra"
    ))

    if not consensus:
        print()
        if blocked:
            print("Every recommended player is already rostered in this league:")
            for pid in blocked[:12]:
                owner = next((r for r in rosters
                              if pid in (r.get("players") or [])), {})
                mine_flag = " (yours)" if owner is mine else ""
                print(f"  {sc.player_label(players, pid)}{mine_flag}")
            print()
            print("Nothing to do here — try a deeper-cut waiver article.")
        elif wide_consensus:
            print("Recommended players were found, but none are rosterable")
            print("(no NFL team, or a non-fantasy position).")
        else:
            print("No player recommendations were parsed from the text at all.")
            print()
            print("Most likely causes, in order:")
            print("  1. The copied text was not the article body (a paywall")
            print("     page, a cookie banner, or an empty clipboard).")
            print("  2. The article lists players only in a table or image,")
            print("     which carries no readable sentence structure.")
            print("  3. Names are written differently than Sleeper spells them.")
            print()
            print("Check what actually got copied with:  pbpaste | head -40")
        return

    def sort_key(item):
        pid, info = item
        # Consensus first, then whether it fills a thin spot, then raw quality.
        pos = (players.get(pid) or {}).get("position")
        fills_need = depth.get(pos, (0, 0, "ok"))[2] == "thin"
        return (info["count"], fills_need, wa.score_player(players.get(pid, {}),
                                                           trending.get(pid, 0)))

    ordered = sorted(consensus.items(), key=sort_key, reverse=True)

    print()
    print(f"AVAILABLE HERE, RECOMMENDED BY YOUR SOURCES ({len(ordered)}):")
    for pid, info in ordered:
        pos = (players.get(pid) or {}).get("position", "?")
        need = depth.get(pos, (0, 0, "ok"))[2]
        faab = f"{info['faab_median']}%" if info["faab_median"] is not None else "n/a"
        print(f"  {sc.player_label(players, pid):<36} "
              f"{info['count']}x  FAAB {faab:<6} {pos} is {need}"
              f"   [{', '.join(info['sources'])}]")

    print()
    print(f"SUGGESTED MOVES (top {max_moves} — for your approval, nothing submitted):")
    protect, spent, made = set(), 0, 0
    for pid, info in ordered:
        if made >= max_moves:
            break
        drops = choose_drop(mine, players, trending, depth, protect)
        if not drops:
            print("  (no droppable bench players left)")
            break
        _, drop_score, drop_pid, drop_player, drop_label = drops[0]
        add_pos = (players.get(pid) or {}).get("position", "?")

        made += 1
        print()
        print(f"  MOVE {made}")
        print(f"    ADD   {sc.player_label(players, pid)}")
        print(f"          {info['count']} of your sources recommend him"
              f" ({', '.join(info['sources'])})")
        if info["faab_values"]:
            print(f"          Analyst FAAB: {info['faab_values']}"
                  f" -> median {info['faab_median']}%")
        print(f"    DROP  {sc.player_label(players, drop_pid)}")
        print(f"          weakest expendable bench piece; "
              f"{drop_player.get('position','?')} is {drop_label} for you")
        if remaining and info["faab_median"] is not None:
            bid = max(1, round(remaining * info["faab_median"] / 100))
            spent += bid
            print(f"    BID   {bid} FAAB ({info['faab_median']}% of {remaining} left)"
                  + (f"   running total {spent}" if made > 1 else ""))
        elif remaining:
            print("    BID   no analyst figure — 1-2 FAAB is a reasonable flier")
        if add_pos in FLEX_ELIGIBLE and depth.get(add_pos, (0, 0, ""))[2] == "thin":
            print(f"    NOTE  fills a thin spot at {add_pos}")
        protect.add(drop_pid)

    if spent > remaining > 0:
        print()
        print(f"  ! These bids total {spent} but you only have {remaining} FAAB.")
        print("    Take them in order and stop when the budget runs out.")

    for pid, info in ordered[:3]:
        for source, ctx in info["contexts"][:1]:
            print()
            print(f"  Why {sc.player_label(players, pid)} — {source}:")
            print(f"    \"{ctx}\"")


def main():
    args = sys.argv[1:]
    if not args or args[0].startswith("--"):
        print(__doc__.strip().split("WHY IT WORKS")[0].strip())
        return 1
    username = args[0].strip().lstrip("@")

    urls = [args[i + 1] for i, a in enumerate(args) if a == "--url" and i + 1 < len(args)]
    directory = None
    if "--articles" in args:
        try:
            directory = args[args.index("--articles") + 1]
        except IndexError:
            print("--articles needs a directory path")
            return 1
    max_moves = 3
    if "--moves" in args:
        try:
            max_moves = int(args[args.index("--moves") + 1])
        except (IndexError, ValueError):
            print("--moves needs a number")
            return 1
    use_stdin = "--stdin" in args
    if not urls and not directory and not use_stdin:
        print("Give me something to read:")
        print("  pbpaste | python3 expert_waivers.py <user> --stdin")
        print("  python3 expert_waivers.py <user> --articles <dir>")
        print("  python3 expert_waivers.py <user> --url <url>")
        print()
        print("Pasting or saving text is more reliable than fetching - many")
        print("fantasy sites block automated requests.")
        return 1

    texts = {}
    if use_stdin:
        if sys.stdin.isatty():
            print("--stdin expects piped text, e.g.:")
            print("  pbpaste | python3 expert_waivers.py <user> --stdin")
            return 1
        pasted = sys.stdin.read().strip()
        if not pasted:
            print("Nothing arrived on stdin - is the clipboard empty?")
            return 1
        texts["pasted"] = strip_html(pasted) if "<" in pasted[:400] else pasted
    if directory:
        texts.update(load_article_files(directory))
    for url in urls:
        print(f"Fetching {url} ...")
        body = fetch_url(url)
        if body:
            texts[url] = body
    if not texts:
        print("No article text could be loaded.")
        return 1
    print(f"Loaded {len(texts)} source(s): {', '.join(texts)}")
    for label, body in texts.items():
        preview = " ".join(body.split())[:90]
        print(f"  {label}: {len(body):,} chars | starts: {preview!r}")
        if len(body) < 300:
            print(f"  ! {label} is very short for an article — if this is not"
                  " the article body, re-copy it.")

    try:
        state = sc.current_state()
        season, week = state.get("season"), state.get("week") or 1
        print(f"NFL {season}, week {week}")
        user = sc.resolve_user(username)
        leagues = sc.user_leagues(user["user_id"], season)
        if not leagues:
            print("No leagues found.")
            return 1
        print("Loading player database...")
        players = sc.all_players()
        trending = wa.trending_adds()

        for league in leagues:
            analyze_league(league, user["user_id"], players, trending,
                           texts, max_moves)

        print()
        print("=" * 74)
        print("Proposals only — nothing was submitted to any league.")
        print("=" * 74)
        return 0
    except sc.SleeperError as e:
        print(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
