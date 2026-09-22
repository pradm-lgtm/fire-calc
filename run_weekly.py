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
import os
import sys

import defense
import expert_extract as ex
import expert_waivers as ew
import localenv
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


def defense_ranks(players, verbose=False):
    """{player_id: {rank, sources, spread}} from the weekly defense list.

    The analysts rank defenses week by week, which is half of how this
    decision gets made and was the half nothing was fetching. The source is
    already configured for the start/sit check; this asks for the defense
    page alone rather than the whole set.
    """
    import lineup
    import rankings as rk
    urls = [entry["url"] for entry in rk.load_sources()
            if "DEF" in [str(p).upper()
                         for p in (entry.get("positions") or [])]]
    if not urls:
        return {}
    try:
        per_source = lineup.gather_rankings(urls, players, verbose=verbose)
    except Exception:
        return {}
    return rk.merge(per_source).get("DEF") or {}


def week_outlook(season, week, players=None, verbose=False):
    """Projections for this week and next, next week's games, and DST ranks.

    Two projection calls and the analysts' defense page. The second week is
    the whole point of the look-ahead: a defense with two good matchups is
    worth more than one with a single good week, and nothing else on hand
    says who anyone plays next week.
    """
    import nfl_week
    out = {"points": {}, "next_points": {}, "next_games": {}, "dst_ranks": {}}
    try:
        out["points"] = nfl_week.points_from(
            nfl_week.projection_rows(season, week))
    except Exception:
        pass
    try:
        ahead = nfl_week.projection_rows(season, int(week) + 1)
        out["next_points"] = nfl_week.points_from(ahead)
        out["next_games"] = nfl_week.matchups_from(ahead)
    except Exception:
        pass
    if players:
        out["dst_ranks"] = defense_ranks(players, verbose)
    return out


def defense_proposal(league, mine, players, rosters, weeks, remaining,
                      week=1):
    """A defense worth streaming this week, as a proposal, or None.

    Kept apart from the article reasoning because it answers a different
    question. Nobody writes up a defense as a player worth owning; they are
    picked on who they play, and the projections already say that.
    """
    found = defense.suggest(league, mine, players, rosters,
                            weeks.get("points") or {},
                            weeks.get("next_points") or {},
                            consensus=weeks.get("dst_ranks"),
                            matchups=weeks.get("next_games"))
    if not found:
        return None
    add_id, drop_id, reason = found
    # Streaming is cheap and meant to stay cheap: a rental for one week is
    # not worth a bid that limits what you can do about a real player.
    bid = min(2, remaining) if remaining else None
    options = []
    if drop_id:
        options = [{
            "id": drop_id,
            "name": sc.player_label(players, drop_id),
            "position": defense.DEF,
            "starter": True,
            "why": "the defense you are streaming now",
        }]
    return dict(
        platform="sleeper",
        league_id=league["league_id"],
        league_name=league.get("name"),
        league_note=league_note(league, rosters),
        add_player_id=add_id,
        add_player_name=sc.player_label(players, add_id),
        add_position=defense.DEF,
        drop_player_id=drop_id,
        drop_player_name=sc.player_label(players, drop_id) if drop_id else None,
        drop_position=defense.DEF if drop_id else None,
        bid=bid, max_bid=remaining, bid_low=None, bid_high=None,
        drop_options=options,
        consensus=0, sources=[],
        rationale=f"Streaming a defense \u2014 {reason}.",
        quote="", rank=99,
    )


def proposals_for_league(league, user_id, players, trending, texts, max_moves,
                         week=1, byes=None, weeks=None):
    """(proposals, why none) - the reasoning, returned as data not text.

    A league that yields nothing says why. There are five ways to come back
    empty here and they mean entirely different things: nobody was written
    up, nobody can be dropped, nobody available is better than what you
    already have. Returning a bare empty list for all of them meant a league
    could stop producing proposals for a fortnight and look exactly like a
    league with a settled roster.
    """
    rosters = sc.league_rosters(league["league_id"])
    mine = sc.my_roster(rosters, user_id)
    if not mine:
        return [], "your roster is not in this league"

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
    # A defense is picked on who it plays, not on who wrote about it, so
    # this is settled before the articles are consulted and survives a week
    # where they turned up nothing.
    streamed = defense_proposal(league, mine, players, rosters, weeks or {},
                                remaining, week)

    consensus = ex.merge_sources(per_source)
    if not consensus:
        if streamed:
            return [streamed], None
        return [], ("no article recommended anyone who is actually available "
                    "here")

    depth = ew.positional_depth(league, mine, players)

    # What each player cost in the draft, and whether anyone has written that
    # he is finished. The first protects the people you would not part with;
    # the second is what lets you part with them anyway.
    try:
        cost = sc.draft_cost(league["league_id"])
    except Exception:
        cost = {}
    held = ex.build_gazetteer(players,
                              [str(p) for p in (mine.get("players") or [])])
    per_source_drops = {}
    for source, text in texts.items():
        said = ex.extract_drops(text, held)
        if said:
            per_source_drops[source] = said
    advice = ex.merge_drops(per_source_drops)
    byes = byes or {}

    def resting(pid):
        team = (players.get(pid) or {}).get("team")
        return bool(team) and byes.get(team) == week

    def sort_key(item):
        pid, info = item
        pos = (players.get(pid) or {}).get("position")
        thin = depth.get(pos, (0, 0, "ok"))[2] == "thin"
        return (info["count"], thin,
                wa.score_player(players.get(pid, {}), trending.get(pid, 0)))

    ordered = sorted(consensus.items(), key=sort_key, reverse=True)

    out, protect, budget_left, why = [], set(), remaining, None
    for pid, info in ordered:
        if len(out) >= max_moves:
            break
        # Everyone you could cut, not only the bench and not only the one it
        # picked. Whether there is anyone worth dropping is half the
        # decision, and it was being made for you out of sight.
        candidates = ew.drop_candidates(mine, players, trending, depth,
                                        cost=cost, week=week)
        drops = ew.choose_drop(mine, players, trending, depth, protect)
        if drops:
            _, drop_score, drop_pid, drop_player, drop_label = drops[0]
        else:
            # The bench is used up or is entirely never-drops. That decided
            # the whole league produced nothing, while the card underneath
            # would happily have offered a starter - the gate was stricter
            # than the choice it was gating.
            spare = [c for c in candidates if c[2] not in protect]
            if not spare:
                why = "there is nobody on the roster left to drop"
                break
            _rank, drop_score, drop_pid, drop_player, drop_label, _st = spare[0]
        place = standings(candidates)
        options = [{
            "id": cid,
            "name": sc.player_label(players, cid),
            "position": p.get("position"),
            "starter": starts,
            "why": drop_reason(p, label, depth, starts,
                               *place.get(cid, (None, None)),
                               cost=cost.get(cid), advice=advice.get(cid),
                               on_bye=resting(cid)),
        } for _rank, _score, cid, p, label, starts in candidates]
        add_score = wa.score_player(players.get(pid, {}), trending.get(pid, 0))
        if drop_score > add_score * 1.5:
            best = strip_label(sc.player_label(players, pid))
            why = (f"the best player available ({best}) is not worth more "
                   "than the weakest player you would have to drop")
            break

        bid = None
        if budget_left and info["faab_median"] is not None:
            bid = max(1, round(remaining * info["faab_median"] / 100))
            if bid > budget_left:
                why = (f"the next claim would cost {bid} FAAB and only "
                       f"{budget_left} is left")
                break
            budget_left -= bid
        elif remaining:
            bid = 1

        add = players.get(pid) or {}
        # The first source that actually said something. Sources that only
        # listed him contribute nothing to quote.
        quote = next((text for _s, text in info["contexts"] if text), "")

        # What the analysts themselves bid, as a range, so the number in the
        # box has something to be judged against.
        faabs = info.get("faab_values") or []
        low = high = None
        if faabs and remaining:
            low = max(1, round(remaining * min(faabs) / 100))
            high = max(low, round(remaining * max(faabs) / 100))

        why = depth_sentence(drop_player, drop_label, depth,
                             sc.player_label(players, drop_pid))
        out.append(dict(
            platform="sleeper",
            league_id=league["league_id"],
            league_name=league.get("name"),
            league_note=league_note(league, rosters),
            add_player_id=pid,
            add_player_name=sc.player_label(players, pid),
            add_position=add.get("position"),
            drop_player_id=drop_pid,
            drop_player_name=sc.player_label(players, drop_pid),
            drop_position=drop_player.get("position"),
            bid=bid, max_bid=remaining, bid_low=low, bid_high=high,
            drop_options=options,
            consensus=info["count"], sources=info["sources"],
            rationale=why,
            quote=quote, rank=len(out) + 1,
        ))
        protect.add(drop_pid)

    # Appended outside the loop: a defense is not competing with the skill
    # players for a roster spot, because it replaces itself.
    if streamed:
        out.append(streamed)
        why = None

    return out, (why if not out else None)


def league_note(league, rosters):
    """'10-team, half-PPR' - what the league's own name does not say.

    A league called LEHG tells you nothing about the rules the advice was
    computed under, and those rules are why a bid or a drop makes sense.
    """
    try:
        import trade_values as tv
        settings = tv.settings_from(league)
    except Exception:
        return None
    teams = len(rosters) or settings.get("teams")
    ppr = settings.get("ppr")
    scoring = {0: "standard", 0.5: "half-PPR", 1: "full PPR"}.get(
        ppr, f"{ppr} per reception" if ppr is not None else None)
    bits = [f"{teams}-team" if teams else None, scoring,
            "superflex" if settings.get("quarterbacks", 1) > 1 else None]
    return ", ".join(b for b in bits if b) or None


def standings(candidates):
    """{player id: (place, how many at his position)}, weakest first.

    The list arrives weakest-first overall but with a depth penalty mixed in,
    so position by position it has to be re-sorted on value alone before it
    can say which of two men at one position is the weaker.
    """
    by_pos = {}
    for _rank, score, pid, player, _label, _starts in candidates:
        by_pos.setdefault(player.get("position") or "?", []).append((score, pid))
    out = {}
    for _pos, rows in by_pos.items():
        rows.sort()
        for i, (_score, pid) in enumerate(rows, start=1):
            out[pid] = (i, len(rows))
    return out


def ordinal(n):
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def standing(place, total, pos):
    """'weakest of your 5 RBs' - the thing that tells two candidates apart.

    Every candidate at one position used to carry the same sentence, which
    said why the position was droppable and nothing at all about why this
    man rather than the one below him.
    """
    if not place or not total:
        return f"on your roster at {pos}"
    if total == 1:
        return f"your only {pos}"
    if place == 1:
        return f"weakest of your {total} {pos}s"
    if place == total:
        return f"your best {pos}"
    return f"{ordinal(place)} weakest of your {total} {pos}s"


def depth_clause(label):
    """What the position's depth means for cutting one, in plain words.

    "RB is deep" was true and unreadable. It means you roster more of them
    than you can start, which is the whole argument for cutting one, so it
    says that. The count is not repeated here: whatever this is added to has
    already given it.
    """
    if label in ("deep", "extra"):
        return "more than you can start"
    if label == "thin":
        return "and you are already thin there"
    return ""


def paid_note(cost):
    """'drafted in round 6' or 'you paid $24 for him', or nothing."""
    if not cost:
        return ""
    amount = cost.get("amount")
    if isinstance(amount, int) and amount > 0:
        return f"you paid ${amount} for him"
    rnd = cost.get("round")
    if isinstance(rnd, int) and rnd > 0:
        return f"drafted in round {rnd}"
    return ""


def advice_note(advice):
    """What the analysts say about cutting him, if any of them said anything.

    The counterweight to the line above it. What you paid is a reason to keep
    somebody, and the reason you will keep him too long; somebody else saying
    he is finished is the thing worth putting next to it.
    """
    if not advice:
        return ""
    count = advice.get("count") or 0
    if count < 1:
        return ""
    who = "1 analyst says" if count == 1 else f"{count} analysts say"
    return f"{who} to let him go"


def drop_reason(player, label, depth, starting=False, place=None, total=None,
                cost=None, advice=None, on_bye=False):
    """The whole case for cutting this particular man, in one clause.

    Every candidate carries its own, so the card can restate the argument
    when you pick somebody else and it stays true. Ordered worst news first:
    being hurt or being in your lineup changes the decision, where the
    position's depth is only background.
    """
    pos = player.get("position") or "?"
    hurt = (player.get("injury_status") or "").strip()
    bits = [standing(place, total, pos)]
    # One of them is the whole position, so saying how deep it is adds
    # nothing that "your only TE" has not already said.
    if (total or 0) > 1:
        bits.append(depth_clause(label))
    bits.append(paid_note(cost))
    bits.append(advice_note(advice))
    if starting:
        bits.insert(0, "in your lineup")
    elif on_bye:
        # Not being in the lineup is usually a judgement about a player. On
        # a bye it is a judgement about the calendar, and the card should
        # not let the two look the same.
        bits.insert(0, "on bye this week")
    # Only an injury that outlasts this week. Leading the case for cutting a
    # good receiver with "out" - when he is back next Sunday - is the loudest
    # possible way to say something that should not weigh on the decision at
    # all. The card shows his status next to his name regardless.
    if hurt and wa.keep_multiplier(player) < 1.0:
        bits.insert(0, hurt.lower())
    return ", ".join(b for b in bits if b)


def depth_sentence(player, label, depth, name):
    """The card's one-line reason for rows with no options to choose from."""
    pos = player.get("position") or "that position"
    have = depth.get(pos, (0, 0, label))[0]
    tail = depth_clause(label)
    count = f"one of your {have} {pos}s" if have != 1 else f"your only {pos}"
    return (f"Dropping {strip_label(name)} \u2014 {count}"
            + (f", {tail}" if tail else "") + ".")


def strip_label(name):
    """'Kenny Gainwell (PHI RB)' -> 'Kenny Gainwell'."""
    return str(name or "").split("(")[0].strip() or str(name or "")


def main_for(username, db_path, moves=3, force=False):
    """Run the job programmatically, for the host's own scheduler."""
    return _run(username, [], moves, False, db_path, force)


def main():
    localenv.load()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("username", nargs="?", default=None,
                    help="Sleeper username (or set it in sources.json)")
    ap.add_argument("--url", action="append", default=[])
    ap.add_argument("--moves", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default=str(st.DB_PATH))
    ap.add_argument("--status", action="store_true",
                    help="say what is on the approval page now, and stop")
    ap.add_argument("--force", action="store_true",
                    help="propose again even if this week already has a run")
    ap.add_argument("--update", action="store_true",
                    help="add anything new to this week's run, keeping the "
                         "decisions already made on it")
    args = ap.parse_args()

    if args.status:
        return say_status()

    username = args.username
    if not username:
        print("Give a Sleeper username: python3 run_weekly.py YOUR_USERNAME")
        return 1
    return _run(username, args.url, args.moves, args.dry_run, args.db,
                args.force, args.update)


def say_status():
    """Print what the approval page is actually showing.

    A screenshot cannot tell a page that is wrong from a page that is merely
    old, and two rounds went on guessing which. The number to look at is the
    drop options: three of them means the run predates the change that offers
    the whole roster, and no amount of deploying will alter it - only a new
    run will.
    """
    import cloud_client as cloud
    if not cloud.configured():
        print("FANTASY_API_URL is not set, so there is no hosted page to ask.")
        return 1
    try:
        info = cloud.status()
    except cloud.RemoteError as exc:
        print(f"ERROR: {exc}")
        return 1
    if not info.get("run"):
        print("The page has no proposals at all yet.")
        return 0
    print(f"Run {info['run']} - season {info['season']}, week {info['week']}")
    print(f"  filed        {info['filed']} ({info['age']})")
    print(f"  proposals    {info['proposals']}  {info.get('statuses') or ''}")
    drops = info.get("drop_options") or {}
    print(f"  drop options {drops.get('fewest')} to {drops.get('most')}"
          " per proposal")
    print(f"  database     {info.get('database')}")
    if (drops.get("most") or 0) <= 3:
        print("\n  Three or fewer drop options means this run was worked out")
        print("  before the whole roster was offered. Re-run to replace it:")
        print("      python3 run_weekly.py --force")
    if info.get("last_refresh_failure"):
        fail = info["last_refresh_failure"]
        print(f"\n  The last Re-check waivers failed at {fail['at']}:")
        print(f"      {fail['detail']}")
    return 0


def _run(username, urls, moves, dry_run, db_path, force=False,
         update=False):
    try:
        state = sc.current_state()
        season, week = state.get("season"), sc.current_week(state)
        print(f"NFL {season}, week {week}")

        print("Finding this week's articles...")
        texts = gather_articles(urls, week)
        if not texts:
            print("No articles could be read. Nothing proposed.")
            return 1
        print(f"Read {len(texts)} article(s).")

        user = sc.resolve_user(username)
        leagues = sc.user_leagues(user["user_id"], season)
        players = sc.all_players()
        trending = wa.trending_adds()

        try:
            import nfl_week
            bye_weeks = nfl_week.byes(season)
        except Exception:
            bye_weeks = {}

        weeks = week_outlook(season, week, players, verbose=True)

        all_proposals, quiet = [], {}
        for league in leagues:
            rows, why = proposals_for_league(league, user["user_id"], players,
                                             trending, texts, moves, week,
                                             byes=bye_weeks, weeks=weeks)
            name = league.get("name") or league.get("league_id")
            print(f"  {name}: {len(rows)} proposal(s)"
                  + (f" — {why}" if why else ""))
            if why:
                quiet[name] = why
            all_proposals.extend(rows)

        if not all_proposals:
            print("No moves worth proposing this week.")
            if not quiet:
                return 0

        if dry_run:
            print("\n--- dry run, nothing written ---")
            for p in all_proposals:
                print(f"  [{p['league_name']}] ADD {p['add_player_name']}"
                      f" / DROP {p['drop_player_name']}  bid {p['bid']}"
                      f"  ({p['consensus']} src)")
            return 0

        push_to = os.environ.get("FANTASY_API_URL")
        if push_to:
            # Serverless hosts cannot run this job inside a request, so it
            # runs wherever there is time and posts the finished proposals.
            import cloud_client
            result = cloud_client.push_proposals(
                season, week, sorted(texts), all_proposals, force, update,
                note=json.dumps(quiet) if quiet else "")
            if result.get("skipped"):
                print(f"\nHost says: {result['skipped']}")
                return 0
            print(f"\nSent {result.get('written', 0)} new proposal(s) to "
                  f"{cloud_client.base_url()}, "
                  f"{result.get('mode', 'filed as')} run {result.get('run')}.")
            return 0

        conn = st.connect(db_path)
        run_id = st.start_run(conn, season, week, sorted(texts),
                              note=json.dumps(quiet) if quiet else "")
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
