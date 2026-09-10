#!/usr/bin/env python3
"""
The weekly job: find this week's articles, work out the moves, file them for
approval.

    python3 run_weekly.py                  # discover sources from sources.json
    python3 run_weekly.py --url URL ...     # use specific articles instead
    python3 run_weekly.py --moves 4         # proposals per league (default 3)
    python3 run_weekly.py --dry-run         # print, write nothing

Designed to be run unattended from launchd on a Tuesday morning. It writes
proposals to the database and stops. Nothing reaches a league from here: the
approval page is where a person decides, and a separate submitter acts only
on what was approved there.
"""

import argparse
import json
import sys

import expert_extract as ex
import expert_waivers as ew
import sleeper_client as sc
import source_discovery as sd
import store as st
import waiver_analyzer as wa


def gather_articles(urls, week, verbose=True):
    """{url: text} for this week's articles, discovered or given."""
    texts = {}
    if urls:
        for url in urls:
            body = ew.fetch_url(url)
            if body:
                texts[url] = body
        return texts

    for entry in sd.load_config():
        hub = entry["hub"]
        try:
            hits = sd.discover(hub, week, entry.get("limit", 2),
                               fetch=lambda u: ew.fetch_raw(u, quiet=True))
        except Exception as exc:
            if verbose:
                print(f"  ! {entry.get('name', hub)}: discovery failed ({exc})")
            continue
        if not hits:
            if verbose:
                print(f"  - {entry.get('name', hub)}: nothing found")
            continue
        for _, url, title in hits:
            if url in texts:
                continue
            body = ew.fetch_url(url)
            if body:
                texts[url] = body
                if verbose:
                    print(f"  + {entry.get('name', hub)}: {title[:60] or url}")
    return texts


def proposals_for_league(league, user_id, players, trending, texts, max_moves):
    """The same reasoning as the CLI report, returned as data instead of text."""
    rosters = sc.league_rosters(league["league_id"])
    mine = sc.my_roster(rosters, user_id)
    if not mine:
        return []

    settings = league.get("settings") or {}
    budget = settings.get("waiver_budget") or 0
    used = (mine.get("settings") or {}).get("waiver_budget_used", 0)
    remaining = budget - used if budget else 0

    taken = wa.rostered_player_ids(rosters)
    available = [pid for pid, p in players.items()
                 if pid not in taken and wa.is_rosterable(p)]
    gaz = ex.build_gazetteer(players, available)

    per_source = {}
    for source, text in texts.items():
        recs = ex.extract_recommendations(text, gaz, source=source,
                                          players=players)
        if recs:
            per_source[source] = recs
    consensus = ex.merge_sources(per_source)
    if not consensus:
        return []

    depth = ew.positional_depth(league, mine, players)

    def sort_key(item):
        pid, info = item
        pos = (players.get(pid) or {}).get("position")
        thin = depth.get(pos, (0, 0, "ok"))[2] == "thin"
        return (info["count"], thin,
                wa.score_player(players.get(pid, {}), trending.get(pid, 0)))

    ordered = sorted(consensus.items(), key=sort_key, reverse=True)

    out, protect, budget_left = [], set(), remaining
    for pid, info in ordered:
        if len(out) >= max_moves:
            break
        drops = ew.choose_drop(mine, players, trending, depth, protect)
        if not drops:
            break
        _, drop_score, drop_pid, drop_player, drop_label = drops[0]
        add_score = wa.score_player(players.get(pid, {}), trending.get(pid, 0))
        if drop_score > add_score * 1.5:
            break

        bid = None
        if budget_left and info["faab_median"] is not None:
            bid = max(1, round(remaining * info["faab_median"] / 100))
            if bid > budget_left:
                break
            budget_left -= bid
        elif remaining:
            bid = 1

        add = players.get(pid) or {}
        quote = info["contexts"][0][1] if info["contexts"] else ""
        out.append(dict(
            platform="sleeper",
            league_id=league["league_id"],
            league_name=league.get("name"),
            add_player_id=pid,
            add_player_name=sc.player_label(players, pid),
            add_position=add.get("position"),
            drop_player_id=drop_pid,
            drop_player_name=sc.player_label(players, drop_pid),
            drop_position=drop_player.get("position"),
            bid=bid, max_bid=remaining,
            consensus=info["count"], sources=info["sources"],
            rationale=f"{drop_player.get('position','?')} is {drop_label} for you",
            quote=quote, rank=len(out) + 1,
        ))
        protect.add(drop_pid)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("username", nargs="?", default=None,
                    help="Sleeper username (or set it in sources.json)")
    ap.add_argument("--url", action="append", default=[])
    ap.add_argument("--moves", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default=str(st.DB_PATH))
    args = ap.parse_args()

    username = args.username
    if not username:
        print("Give a Sleeper username: python3 run_weekly.py YOUR_USERNAME")
        return 1

    try:
        state = sc.current_state()
        season, week = state.get("season"), state.get("week") or 1
        print(f"NFL {season}, week {week}")

        print("Finding this week's articles...")
        texts = gather_articles(args.url, week)
        if not texts:
            print("No articles could be read. Nothing proposed.")
            return 1
        print(f"Read {len(texts)} article(s).")

        user = sc.resolve_user(username)
        leagues = sc.user_leagues(user["user_id"], season)
        players = sc.all_players()
        trending = wa.trending_adds()

        all_proposals = []
        for league in leagues:
            rows = proposals_for_league(league, user["user_id"], players,
                                        trending, texts, args.moves)
            print(f"  {league.get('name')}: {len(rows)} proposal(s)")
            all_proposals.extend(rows)

        if not all_proposals:
            print("No moves worth proposing this week.")
            return 0

        if args.dry_run:
            print("\n--- dry run, nothing written ---")
            for p in all_proposals:
                print(f"  [{p['league_name']}] ADD {p['add_player_name']}"
                      f" / DROP {p['drop_player_name']}  bid {p['bid']}"
                      f"  ({p['consensus']} src)")
            return 0

        conn = st.connect(args.db)
        run_id = st.start_run(conn, season, week, sorted(texts))
        written = sum(1 for p in all_proposals
                      if st.add_proposal(conn, run_id, **p) is not None)
        conn.close()
        print(f"\nFiled {written} proposal(s) for approval as run {run_id}.")
        print("Review them at:  python3 webapp.py")
        return 0
    except sc.SleeperError as e:
        print(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
