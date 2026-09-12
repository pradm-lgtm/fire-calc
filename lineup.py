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
import localenv
import nfl_week
import rankings as rk
import render
import sleeper_client as sc
import store as st
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
        want = entry.get("positions")
        overall = entry.get("overall", False)
        overall_only = entry.get("overall_only", False)

        def count(ranked):
            """Players ranked, not counting the overall list twice.

            The overall list repeats every player already counted under his
            position, so summing all the buckets reported 858 players read
            out of a 458-row page.
            """
            positional = sum(len(v) for pos, v in ranked.items()
                             if v and pos != rk.OVERALL)
            return positional or len(ranked.get(rk.OVERALL) or {})

        def from_rows(html, label):
            """Rankings read out of the page's own data, and what it cost."""
            rows = rk.ranked_rows_from_html(html)
            if not rows:
                return {}, f"no ranked data in the {label} page"
            ranked = rk.ranks_from_rows(rows, players, gazetteer, want, overall,
                                        overall_only)
            got = count(ranked)
            if got >= 10:
                return ranked, f"read {got} of {len(rows)} rows from the page's own data"
            # Rows but no matches is a different failure from no rows, and
            # the fix is different: a name-matching problem, not a loading one.
            return ranked, (f"{label} page data had {len(rows)} rows but only "
                            f"{got} matched players in your leagues")

        raw = ew.fetch_raw(entry["url"], quiet=not verbose)
        ranked, note = from_rows(raw or "", "raw")
        total = count(ranked)

        if total >= 10 and verbose:
            print(f"  . {entry['name']}: {note} (no browser needed)")

        if total < 10 and raw:
            # No embedded data: fall back to reading the order names appear in.
            ranked = rk.ranks_from_text(ew.strip_html(raw), players, gazetteer,
                                        want, overall, overall_only)
            total = count(ranked)

        if total < 10:
            # A rankings table is usually built by JavaScript, so the HTML a
            # server sends contains no players at all. Run the page properly.
            if verbose:
                print(f"  . {entry['name']}: {note}, loading it in a browser")
            got = render.fetch_rendered_full(entry["url"], quiet=not verbose)
            if got:
                # Prefer the data the page ships over the order it draws:
                # reading the visible order infers rank and quietly truncates
                # whenever a page does not fully render.
                ranked, note = from_rows(got["html"], "rendered")
                total = count(ranked)
                if total < 10:
                    ranked = rk.ranks_from_text(got["text"], players, gazetteer,
                                                want, overall, overall_only)
                    total = count(ranked)
                    if verbose:
                        print(f"    ({note}; read the rendered text instead, "
                              f"{len(got['text']):,} chars)")
                elif verbose:
                    print(f"    ({note})")

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
        elif mine is None and best is None:
            # Neither he nor any alternative appears in the rankings, so
            # there is no opinion to compare against. Calling that green
            # would claim agreement that was never established.
            colour = "UNKNOWN"
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


DOT = {"GREEN": "GREEN ", "YELLOW": "YELLOW", "RED": "RED   ",
       "UNKNOWN": "  ?   "}


def context_for(player, pid, games, points):
    """Opponent and projected points, when they could be read at all."""
    return {"pos": player.get("position"),
            "matchup": games.get((player.get("team") or "").upper()),
            "projection": points.get(str(pid))}


def bench_rows(league, roster, players, consensus, games, points):
    """Everyone not starting, so the page can show what the choice was.

    A verdict that says to bench someone is only actionable next to who
    would replace him, and the page had the name of one alternative and
    nothing else about the rest of the roster.
    """
    starters = [str(p) for p in (roster.get("starters") or []) if p and p != "0"]
    everyone = [str(p) for p in (roster.get("players") or []) if p and p != "0"]
    rows = []
    for i, pid in enumerate(p for p in everyone if p not in starters):
        player = players.get(pid) or {}
        if not player:
            continue
        scale = player.get("position")
        rows.append({
            "league_id": str(league["league_id"]),
            "league_name": league.get("name"),
            "slot": "BN", "position": i, "verdict": "BENCH",
            "role": "bench", "player_id": pid,
            "player_name": sc.player_label(players, pid).split(" [")[0],
            "rank_text": (rk.describe(consensus, pid, scale) if scale
                          else "unranked"),
            "better_name": None,
            "detail": injury_note(player),
            **context_for(player, pid, games, points),
        })
    rows.sort(key=lambda r: (r["projection"] is None, -(r["projection"] or 0)))
    for i, row in enumerate(rows):
        row["position"] = i
    return rows


def flag_rows(league, roster, players, consensus, games=None, points=None):
    """One serialisable row per started player.

    The terminal report and the phone page both read these, so the two can
    never drift into saying different things about the same lineup.
    """
    games, points = games or {}, points or {}
    rows = []
    for i, v in enumerate(assess(league, roster, players, consensus)):
        rank_text = (rk.describe(consensus, v["pid"], v["scale"])
                     if v["scale"] else "unranked")
        reasons = []
        if must_sit(v["player"]):
            reasons.append(f"he is {injury_note(v['player'])} — he will not play")
        better_name = None
        if v["colour"] != "GREEN" and v["better"]:
            better_name = sc.player_label(players, v["better"]).split(" [")[0]
            flexible = len(SLOT_ELIGIBILITY.get(v["slot"], ())) > 1
            b_scale = (rk.OVERALL if flexible
                       else (players.get(v["better"]) or {}).get("position"))
            b_rank = rk.rank_of(consensus, v["better"], b_scale)
            where = f"{int(round(b_rank))}" if b_rank is not None else "?"
            scale_txt = "overall" if b_scale == rk.OVERALL else b_scale
            gap = f", {v['gap']:.0f} places better" if v["gap"] < 90 else ""
            reasons.append(f"consensus prefers {better_name} — "
                           f"{scale_txt} {where}{gap}")
        rows.append({
            "league_id": str(league["league_id"]),
            "league_name": league.get("name"),
            "slot": v["slot"], "position": i, "verdict": v["colour"],
            "player_id": v["pid"],
            "player_name": sc.player_label(players, v["pid"]).split(" [")[0],
            "rank_text": rank_text, "better_name": better_name,
            "detail": "; ".join(reasons),
            "role": "starter",
            **context_for(v["player"], v["pid"], games, points),
        })
    return rows


def league_rows(league, user_id, players, consensus, games=None, points=None):
    rosters = sc.league_rosters(league["league_id"])
    mine = sc.my_roster(rosters, user_id)
    if not mine:
        return None
    return (flag_rows(league, mine, players, consensus, games, points)
            + bench_rows(league, mine, players, consensus,
                         games or {}, points or {}))


def report(league, rows):
    print()
    print("=" * 74)
    print(f"{league.get('name','?')}   ({sc.scoring_summary(league)})")
    print("=" * 74)
    if rows is None:
        print("Could not find your roster here; skipping.")
        return 0

    started = [r for r in rows if r.get("role", "starter") == "starter"]
    flagged = sum(1 for r in started if r["verdict"] in ("YELLOW", "RED"))
    unknown = sum(1 for r in started if r["verdict"] == "UNKNOWN")
    print()
    for r in started:
        print(f"  {DOT[r['verdict']]} {r['slot']:<11} "
              f"{r['player_name']:<36} {r['rank_text']}")
        for reason in filter(None, r["detail"].split("; ")):
            print(f"              {reason}")
    if not flagged:
        if unknown:
            print(f"\n  Nothing to change among the players the rankings "
                  f"cover, but {unknown} could not be judged.")
        else:
            print("\n  Your lineup matches consensus.")
    if unknown:
        print(f"  ({unknown} starter(s) missing from the rankings — the "
              "pages may not go deep enough.)")
    return flagged


class NoRankings(Exception):
    """No ranking page could be read, so there is nothing to compare with."""


def check(username, week=None, urls=(), verbose=True):
    """Run the whole start/sit check. Returns the rows and what produced them.

    Separate from main() because the page runs this itself now: a verdict
    from last Sunday is about a lineup you have since changed.
    """
    state = sc.current_state()
    season = state.get("season")
    week = week or state.get("week") or 1
    players = sc.all_players()

    # Context for the page, never for the verdict: a missing opponent or
    # projection leaves a line blank and changes no colour.
    games = nfl_week.schedule(season, week)
    points = nfl_week.projections(season, week)
    if verbose and not (games and points):
        missing = " and ".join(
            n for n, got in (("opponents", games), ("projections", points))
            if not got)
        print(f"  (no {missing} this time; the page will leave them blank)")

    per_source = gather_rankings(list(urls), players, verbose=verbose)
    if not per_source:
        raise NoRankings("no ranking page could be read")
    consensus = rk.merge(per_source)

    user = sc.resolve_user(username)
    leagues = sc.user_leagues(user["user_id"], season)
    found = []
    for league in leagues:
        found.append((league, league_rows(league, user["user_id"], players,
                                          consensus, games, points)))
    return {"season": season, "week": week, "sources": sorted(per_source),
            "leagues": found,
            "rows": [r for _l, rows in found for r in (rows or [])]}


def store_check(conn, got):
    """Write one check's verdicts. The newest check is the only one shown."""
    check_id = st.start_lineup_check(conn, got["season"], got["week"],
                                     got["sources"])
    for row in got["rows"]:
        st.add_lineup_flag(conn, check_id, **row)
    return check_id


def main():
    localenv.load()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("username")
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--url", action="append", default=[],
                    help="a ranking page to use instead of the configured ones")
    ap.add_argument("--save", action="store_true",
                    help="record the verdicts so the approval page shows them "
                         "(sent to the hosted page when FANTASY_API_URL is set)")
    args = ap.parse_args()

    try:
        print("Reading rankings...")
        got = check(args.username, args.week, args.url)
        print(f"NFL {got['season']}, week {got['week']}")

        flagged = sum(report(league, rows) for league, rows in got["leagues"])

        if args.save:
            import cloud_client
            if cloud_client.configured():
                # Same split as the waiver job: the work happens where there
                # is time for it, and only the finished verdicts travel.
                result = cloud_client.push_lineup(
                    got["season"], got["week"], got["sources"], got["rows"])
                print(f"\nSent {result.get('written', 0)} verdict(s) to "
                      f"{cloud_client.base_url()}.")
            else:
                conn = st.connect()
                store_check(conn, got)
                conn.close()
                print(f"\nSaved {len(got['rows'])} verdicts for the "
                      "approval page.")
        print()
        print("=" * 74)
        print(f"{flagged} started player(s) worth a second look. "
              "Nothing was changed.")
        print("=" * 74)
        return 0
    except NoRankings:
        print("\nNo rankings could be read, so there is nothing to compare")
        print("against. Check the pages with:")
        print("  python3 rankings.py PAGE")
        return 1
    except sc.SleeperError as e:
        print(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
