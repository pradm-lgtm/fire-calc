#!/usr/bin/env python3
"""
Start/sit checked against analyst consensus.

    python3 lineup.py <your-sleeper-username>
    python3 lineup.py pradm7 --week 3
    python3 lineup.py pradm7 --url https://site/half-ppr-rankings

This does not project anything. It reads half-PPR rankings from the sites
you trust and tells you where your lineup disagrees with them, so the
judgement stays yours and the arithmetic does not.

Each started player gets a colour:

  GREEN   nobody on your bench is ranked meaningfully above him
  YELLOW  a bench player ranks somewhat higher - close enough to be a
          matchup call rather than a mistake
  RED     a bench player ranks far higher, or the starter will not play

Rankings are positional and come from the order players appear in on a
ranking page, which is what a ranking page is. Nothing is changed in any
league.
"""

import argparse
import sys

import expert_extract as ex
import expert_waivers as ew
import rankings as rk
import render
import sleeper_client as sc
import waiver_analyzer as wa

# Which positions may fill which lineup slot.
SLOT_ELIGIBILITY = {
    "QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"},
    "K": {"K"}, "DEF": {"DEF"},
    "FLEX": {"RB", "WR", "TE"},
    "WRRB_FLEX": {"RB", "WR"},
    "REC_FLEX": {"WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
}
BENCH_SLOTS = {"BN", "IR", "TAXI"}

# How far a bench player must out-rank a starter before it stops being a
# matchup opinion and starts being a mistake. Positional places, so "8" means
# roughly WR12 sitting behind WR20.
RED_GAP = 8
YELLOW_GAP = 3

# Ranking disagreement is only meaningful relative to depth. Ten places apart
# near the top of a position is a different decision from ten places apart in
# the eighties, where the rankings themselves are guesswork, so the gap has to
# grow with the rank being questioned.
RED_FRACTION = 0.20
YELLOW_FRACTION = 0.08


def thresholds(rank):
    if rank is None:
        return YELLOW_GAP, RED_GAP
    return (max(YELLOW_GAP, rank * YELLOW_FRACTION),
            max(RED_GAP, rank * RED_FRACTION))

SIT_STATUSES = {"out", "ir", "doubtful", "suspended", "pup"}


def gather_rankings(urls, players, verbose=True):
    """{source: {position: {player_id: rank}}} from ranking pages."""
    gazetteer = ex.build_gazetteer(
        players, [pid for pid, p in players.items() if wa.is_rosterable(p)])
    sources = ([{"name": u, "url": u} for u in urls] if urls
               else rk.load_sources())
    per_source = {}
    for entry in sources:
        text = ew.fetch_url(entry["url"])
        want = entry.get("positions")
        ranked = (rk.ranks_from_text(text, players, gazetteer, want,
                                     entry.get("overall", False))
                  if text else {})
        total = sum(len(v) for v in ranked.values() if v)

        if total < 10:
            # A rankings table is usually built by JavaScript, so the HTML a
            # server sends contains no players at all. Run the page properly.
            if verbose:
                print(f"  . {entry['name']}: nothing in the raw HTML, "
                      "loading it in a browser")
            text = render.fetch_rendered(entry["url"], quiet=not verbose)
            ranked = (rk.ranks_from_text(text, players, gazetteer, want,
                                        entry.get("overall", False))
                      if text else {})
            total = sum(len(v) for v in ranked.values() if v)

        if total < 10:
            if verbose:
                print(f"  - {entry['name']}: only {total} players found "
                      "even after rendering; skipping")
            continue
        per_source[entry["name"]] = ranked
        if verbose:
            shape = ", ".join(f"{p}{len(v)}" for p, v in sorted(ranked.items()))
            print(f"  + {entry['name']}: {total} ranked ({shape})")
    return per_source


def injury_note(player):
    status = (player.get("injury_status") or "").strip()
    return status or ""


def must_sit(player):
    return injury_note(player).lower() in SIT_STATUSES


def starting_slots(league):
    return [s for s in (league.get("roster_positions") or [])
            if s not in BENCH_SLOTS]


def eligible(player, slot):
    pos = player.get("position")
    allowed = SLOT_ELIGIBILITY.get(slot)
    if not allowed:
        return False
    if pos in allowed:
        return True
    return bool(set(player.get("fantasy_positions") or []) & allowed)


def assess(league, roster, players, consensus):
    """One verdict per started player, with the bench player behind it."""
    slots = starting_slots(league)
    starters = [str(p) for p in (roster.get("starters") or []) if p and p != "0"]
    everyone = [str(p) for p in (roster.get("players") or []) if p and p != "0"]
    bench = [p for p in everyone if p not in starters]

    verdicts, claimed = [], set()
    for i, pid in enumerate(starters):
        slot = slots[i] if i < len(slots) else "?"
        player = players.get(pid) or {}
        pos = player.get("position")
        # A flex slot pits positions against each other, so compare where the
        # page itself put them; a fixed slot compares within its position.
        flexible = len(SLOT_ELIGIBILITY.get(slot, ())) > 1
        scale = rk.OVERALL if flexible else pos
        mine = rk.rank_of(consensus, pid, scale) if scale else None

        best, best_gap = None, 0.0
        for cand_id in bench:
            if cand_id in claimed:
                continue
            cand = players.get(cand_id) or {}
            if not cand or not eligible(cand, slot) or must_sit(cand):
                continue
            cand_scale = rk.OVERALL if flexible else cand.get("position")
            cand_rank = rk.rank_of(consensus, cand_id, cand_scale)
            if cand_rank is None:
                continue
            if must_sit(player):
                # Anyone playable beats someone who will not play.
                gap = 99.0 if mine is None else max(1.0, mine - cand_rank)
            elif mine is None:
                # An unranked starter behind a ranked alternative is worth
                # raising, but it is not the same as being out-ranked.
                gap = float(YELLOW_GAP)
            else:
                gap = mine - cand_rank
            if gap > best_gap:
                best, best_gap = cand_id, gap

        yellow_at, red_at = thresholds(mine)
        if must_sit(player):
            colour = "RED"
        elif best is None or best_gap < yellow_at:
            colour = "GREEN"
        elif best_gap >= red_at:
            colour = "RED"
        else:
            colour = "YELLOW"
        if colour != "GREEN" and best:
            claimed.add(best)
        verdicts.append({"slot": slot, "pid": pid, "player": player,
                         "rank": mine, "colour": colour, "scale": scale,
                         "better": best, "gap": best_gap})
    return verdicts


DOT = {"GREEN": "GREEN ", "YELLOW": "YELLOW", "RED": "RED   "}


def report(league, user_id, players, consensus):
    print()
    print("=" * 74)
    print(f"{league.get('name','?')}   ({sc.scoring_summary(league)})")
    print("=" * 74)

    rosters = sc.league_rosters(league["league_id"])
    mine = sc.my_roster(rosters, user_id)
    if not mine:
        print("Could not find your roster here; skipping.")
        return 0

    verdicts = assess(league, mine, players, consensus)
    flagged = 0
    print()
    for v in verdicts:
        rank_txt = (rk.describe(consensus, v["pid"], v["scale"])
                    if v["scale"] else "unranked")
        print(f"  {DOT[v['colour']]} {v['slot']:<11} "
              f"{sc.player_label(players, v['pid']):<36} {rank_txt}")
        if v["colour"] == "GREEN":
            continue
        flagged += 1
        if must_sit(v["player"]):
            print(f"              he is {injury_note(v['player'])} — "
                  "he will not play")
        if v["better"]:
            better = sc.player_label(players, v["better"]).split(" [")[0]
            b_scale = (rk.OVERALL if len(SLOT_ELIGIBILITY.get(v["slot"], ())) > 1
                       else (players.get(v["better"]) or {}).get("position"))
            b_rank = rk.rank_of(consensus, v["better"], b_scale)
            where = (f"{int(round(b_rank))}" if b_rank is not None else "?")
            scale_txt = "overall" if b_scale == rk.OVERALL else b_scale
            gap = f", {v['gap']:.0f} places better" if v["gap"] < 90 else ""
            print(f"              consensus prefers {better} "
                  f"— {scale_txt} {where}{gap}")
    if not flagged:
        print("\n  Your lineup matches consensus.")
    return flagged


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("username")
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--url", action="append", default=[],
                    help="a ranking page to use instead of the configured ones")
    args = ap.parse_args()

    try:
        state = sc.current_state()
        season = state.get("season")
        week = args.week or state.get("week") or 1
        print(f"NFL {season}, week {week}")
        players = sc.all_players()

        print("Reading rankings...")
        per_source = gather_rankings(args.url, players)
        if not per_source:
            print("\nNo rankings could be read, so there is nothing to compare")
            print("against. Check the pages with:")
            print("  python3 lineup.py USER --url PAGE")
            return 1
        consensus = rk.merge(per_source)

        user = sc.resolve_user(args.username)
        leagues = sc.user_leagues(user["user_id"], season)
        flagged = sum(report(l, user["user_id"], players, consensus)
                      for l in leagues)
        print()
        print("=" * 74)
        print(f"{flagged} started player(s) worth a second look. "
              "Nothing was changed.")
        print("=" * 74)
        return 0
    except sc.SleeperError as e:
        print(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
