#!/usr/bin/env python3
"""Tests for the waiver review page and the reasons it shows.

    python3 test_waivers.py

The quote is the part worth guarding. A ranking page wraps its tables in
furniture that names a dozen players and analyses none of them, and one of
those arrived on a card as the reason to spend real FAAB.
"""

import json
import re
import unittest
from datetime import datetime, timedelta, timezone

import expert_extract as ex
import expert_waivers as ew
import run_weekly as rw
import store as st
import waiver_analyzer as wa
import webapp

LISTING = ("Below are some popular searches and comparisons from our Who To "
           "Pickup tool for 2026 for George Holani, Kaleb Johnson, Kaelon "
           "Black, Jalen Coker, Caleb Douglas, Terrance Ferguson, and Malik "
           "Willis")
WRITEUP = ("Ferguson saw eight targets in his first extended run and should "
           "be rostered in every format this week.")


class Quotes(unittest.TestCase):

    def test_a_listing_is_not_a_reason(self):
        self.assertFalse(ex.usable_quote(LISTING, 6))

    def test_site_furniture_is_rejected_even_naming_one_player(self):
        self.assertFalse(ex.usable_quote(
            "Click here to see the full rankings for Terrance Ferguson.", 0))

    def test_a_crowd_of_names_is_a_list_not_analysis(self):
        self.assertFalse(ex.usable_quote(
            "Ferguson, Holani, Johnson, Black, Coker and Douglas are all "
            "available in most leagues right now.", 5))

    def test_actual_analysis_survives(self):
        self.assertTrue(ex.usable_quote(WRITEUP, 0))

    def test_a_fragment_is_not_enough(self):
        self.assertFalse(ex.usable_quote("Add him.", 0))


class Card(unittest.TestCase):

    def build(self, *overrides):
        conn = st.connect(":memory:")
        run_id = st.start_run(conn, "2026", 2, ["FantasyPros"])
        base = dict(league_id="1", league_name="OTG Alumni",
                    add_player_id="1", add_player_name="Ferguson (LAR TE)",
                    add_position="TE", drop_player_id="2",
                    drop_player_name="Some Guy (NYJ WR)", drop_position="WR",
                    bid=6, max_bid=100, bid_low=3, bid_high=11, consensus=2,
                    sources=["https://www.fantasypros.com/x"],
                    rationale="WR is deep",
                    quote="")
        for i, over in enumerate(overrides or [{}]):
            row = dict(base, add_player_id=str(10 + i))
            row.update(over)
            st.add_proposal(conn, run_id, **row)
        return webapp.render(conn).decode()

    def test_no_write_up_says_so_instead_of_quoting_furniture(self):
        self.assertIn("Limited recent coverage", self.build())

    def test_a_stored_listing_is_refused_at_the_page_too(self):
        # The quote was taken before the extractor learned to reject this,
        # and it is still in the database.
        html = self.build({"quote": LISTING})
        self.assertNotIn("popular searches", html)
        self.assertIn("Limited recent coverage", html)

    def test_a_quote_about_somebody_else_is_refused(self):
        html = self.build({"quote": "Alec Pierce cleared concussion protocol "
                                    "and should be rostered everywhere."})
        self.assertIn("Limited recent coverage", html)

    def test_a_real_write_up_is_shown(self):
        self.assertIn("eight targets", self.build({"quote": WRITEUP}))

    def test_the_drop_reason_stays_short(self):
        self.assertIn("WR is deep", self.build())

    def test_the_button_names_the_number_beside_it(self):
        self.assertIn("Approve at <span data-approve-bid>6</span>",
                      self.build())

    def test_the_analyst_range_is_shown(self):
        self.assertIn("Analysts bid 3 to 11", self.build())

    def test_no_analyst_number_is_admitted_rather_than_invented(self):
        html = self.build({"bid_low": None, "bid_high": None})
        self.assertIn("No analyst put a number on him", html)

    def test_the_budget_line_counts_every_pending_bid(self):
        # The number you need before approving three bids in one league is
        # what is left after all three, not what is left now.
        html = self.build({"bid": 6}, {"bid": 14}, {"bid": 4})
        bar = re.search(r"class='bar'[^>]*>(.*?)</div>", html).group(1)
        self.assertIn("approving all 3 costs <strong data-cost>24</strong>",
                      bar)
        self.assertIn("leaving <strong data-after>76</strong>", bar)

    def test_bids_beyond_the_budget_are_called_out(self):
        html = self.build({"bid": 60, "max_bid": 100},
                          {"bid": 70, "max_bid": 100})
        self.assertIn("more than you have", html)

    def test_the_refresh_reads_as_a_refresh(self):
        html = self.build()
        self.assertIn("Re-check waivers", html)
        self.assertNotIn("Work them out again", html)


OPTIONS = [{"id": "d1", "name": "J.K. Dobbins (DEN RB)", "position": "RB",
            "why": "weakest of your 5 RBs"},
           {"id": "d2", "name": "Alec Pierce (IND WR)", "position": "WR",
            "why": "3rd weakest of your 7 WRs"}]


class Drops(unittest.TestCase):
    """Choosing who goes, rather than being told."""

    def build(self, *proposals):
        conn = st.connect(":memory:")
        run = st.start_run(conn, "2026", 2, ["ESPN"])
        for i, over in enumerate(proposals or [{}]):
            row = dict(league_id="1", league_name="LEHG",
                       add_player_id=f"a{i}", add_player_name=f"Add {i} (LV RB)",
                       add_position="RB", drop_player_id="d1",
                       drop_player_name="J.K. Dobbins (DEN RB)",
                       drop_position="RB", bid=5, max_bid=100, consensus=2,
                       sources=["https://espn.com/x"],
                       rationale="You roster 5 RBs, more than you can start "
                                 "&mdash; J.K. Dobbins is the weakest of them.",
                       drop_options=OPTIONS)
            row.update(over)
            st.add_proposal(conn, run, **row)
        return conn

    def test_the_drop_is_named_without_opening_anything(self):
        # Who goes is half of what you are approving, so it is on the card.
        html = webapp.render(self.build()).decode()
        summary = re.search(r"<summary>(.*?)</summary>", html).group(1)
        self.assertIn("J.K. Dobbins", summary)
        self.assertIn("Change", summary)

    def test_the_drop_is_a_choice_with_reasons_beside_it(self):
        html = webapp.render(self.build()).decode()
        self.assertIn("type='radio'", html)
        self.assertIn("Alec Pierce", html)
        self.assertIn("weakest of your 5 RBs", html)

    def test_the_recommended_drop_is_marked_as_such(self):
        html = webapp.render(self.build()).decode()
        self.assertIn("Recommended", html)

    def test_the_suggested_drop_is_preselected(self):
        html = webapp.render(self.build()).decode()
        self.assertIn("value='d1' checked", html)

    def test_no_native_select_covers_the_bid_box(self):
        self.assertNotIn("<select", webapp.render(self.build()).decode())

    def test_choosing_a_different_drop_is_recorded(self):
        conn = self.build()
        row = st.proposals_for_run(conn, st.latest_run(conn)["id"])[0]
        st.decide(conn, row["id"], st.APPROVED, bid=5,
                  drop_player_id="d2",
                  drop_player_name=webapp.drop_name(conn, row["id"], "d2"))
        after = st.proposals_for_run(conn, st.latest_run(conn)["id"])[0]
        self.assertEqual(after["drop_player_id"], "d2")
        self.assertEqual(after["drop_player_name"], "Alec Pierce (IND WR)")

    def test_the_position_travels_with_the_name(self):
        # A drop swapped from a back to a receiver kept the old chip.
        conn = self.build()
        row = st.proposals_for_run(conn, st.latest_run(conn)["id"])[0]
        pick = webapp.drop_pick(conn, row["id"], "d2")
        st.decide(conn, row["id"], st.APPROVED, bid=5,
                  drop_player_id="d2", drop_player_name=pick["name"],
                  drop_position=pick["position"])
        after = st.proposals_for_run(conn, st.latest_run(conn)["id"])[0]
        self.assertEqual(after["drop_player_name"], "Alec Pierce (IND WR)")
        self.assertEqual(after["drop_position"], "WR")

    def test_the_name_comes_from_the_options_not_the_form(self):
        # So a posted form cannot name one player and identify another.
        conn = self.build()
        row = st.proposals_for_run(conn, st.latest_run(conn)["id"])[0]
        self.assertIsNone(webapp.drop_name(conn, row["id"], "not-an-option"))

    def test_proposals_are_ordered_by_what_they_cost(self):
        conn = self.build({"bid": 4}, {"bid": 12}, {"bid": 7})
        bids = [r["bid"] for r in
                st.proposals_for_run(conn, st.latest_run(conn)["id"])]
        self.assertEqual(bids, [12, 7, 4])

    def body(self, *overrides):
        """The page without its stylesheet, which names every class."""
        return webapp.render(self.build(*overrides)).decode().split(
            "</style></head>")[1]

    def test_claims_sharing_a_drop_are_called_fallbacks(self):
        # Whichever is higher in the queue takes him, so the one below only
        # lands if it fails and the budget total above is the worst case.
        body = self.body({}, {})
        self.assertIn("one of these is a fallback and only runs if the "
                      "claim above it fails", body)
        self.assertIn("Fallback", body)

    def test_distinct_drops_are_not_flagged(self):
        body = self.body({}, {"drop_player_id": "d2",
                              "drop_player_name": "Alec Pierce (IND WR)"})
        self.assertNotIn("is a fallback", body)
        self.assertNotIn("Fallback", body)


class Reasons(unittest.TestCase):
    """The words on the card, and what tells two drop candidates apart."""

    DEPTH = {"RB": (5, 2.3, "deep"), "TE": (1, 1.0, "thin"),
             "WR": (6, 3.0, "ok")}

    def reason(self, pos, place, total, label, **player):
        return rw.drop_reason(dict(player, position=pos), label, self.DEPTH,
                              player.get("starting", False), place, total)

    def test_two_candidates_at_one_position_read_differently(self):
        first = self.reason("RB", 1, 5, "deep")
        second = self.reason("RB", 2, 5, "deep")
        self.assertNotEqual(first, second)
        self.assertIn("weakest of your 5 RBs", first)
        self.assertIn("2nd weakest of your 5 RBs", second)

    def test_the_shorthand_is_spelled_out(self):
        self.assertIn("more than you can start", self.reason("RB", 1, 5, "deep"))
        self.assertNotIn("is deep", self.reason("RB", 1, 5, "deep"))

    def test_depth_is_not_repeated_for_a_position_of_one(self):
        self.assertEqual(self.reason("TE", 1, 1, "thin"), "your only TE")

    def test_being_hurt_leads_when_the_injury_outlasts_the_week(self):
        why = self.reason("WR", 3, 6, "ok", injury_status="IR")
        self.assertTrue(why.startswith("ir"), why)

    def test_a_this_week_injury_does_not_lead(self):
        """It used to. "Questionable" is not an argument for cutting anyone."""
        why = self.reason("WR", 3, 6, "ok", injury_status="Questionable")
        self.assertFalse(why.startswith("questionable"), why)

    def test_standings_rank_within_a_position_not_across_the_roster(self):
        cands = [
            (0, 1.0, "rb1", {"position": "RB"}, "deep", False),
            (0, 4.0, "rb2", {"position": "RB"}, "deep", False),
            (0, 2.0, "wr1", {"position": "WR"}, "ok", False),
        ]
        place = rw.standings(cands)
        self.assertEqual(place["rb1"], (1, 2))
        self.assertEqual(place["rb2"], (2, 2))
        self.assertEqual(place["wr1"], (1, 1))

    def test_the_league_note_says_what_the_name_does_not(self):
        note = rw.league_note(
            {"settings": {"num_teams": 10}, "scoring_settings": {"rec": 0.5},
             "roster_positions": ["QB", "RB"]}, [1] * 10)
        self.assertEqual(note, "10-team, half-PPR")


class Page(unittest.TestCase):
    """How a card reads at a glance."""

    OPTS = [{"id": "g", "name": "Kenny Gainwell (PHI RB)", "position": "RB",
             "why": "weakest of your 5 RBs, more than you can start"},
            {"id": "d", "name": "JK Dobbins (DEN RB)", "position": "RB",
             "why": "2nd weakest of your 5 RBs, more than you can start"}]

    def build(self, **over):
        conn = st.connect(":memory:")
        run = st.start_run(conn, "2026", 3, ["ESPN"])
        row = dict(league_id="1", league_name="LEHG",
                   league_note="10-team, half-PPR",
                   add_player_id="v", add_player_name="Devaughn Vele (NO WR)",
                   add_position="WR", drop_player_id="g",
                   drop_player_name="Kenny Gainwell (PHI RB)",
                   drop_position="RB", bid=9, max_bid=100, consensus=2,
                   sources=["https://espn.com/x"], rationale="", quote="",
                   drop_options=self.OPTS)
        row.update(over)
        st.add_proposal(conn, run, **row)
        return webapp.render(conn).decode().split("</style></head>")[1]

    def test_the_reason_names_the_drop_that_is_selected(self):
        body = self.build()
        self.assertIn("Dropping <b data-dropwho>Kenny Gainwell</b>", body)
        self.assertIn("weakest of your 5 RBs", body)

    def test_choosing_the_other_drop_changes_the_reason(self):
        body = self.build(drop_player_id="d",
                          drop_player_name="JK Dobbins (DEN RB)")
        self.assertIn("Dropping <b data-dropwho>JK Dobbins</b>", body)
        self.assertIn("2nd weakest", body)

    def test_the_league_note_is_shown_under_its_name(self):
        self.assertIn("10-team, half-PPR", self.build())

    def test_the_labels_do_not_shout(self):
        # Hierarchy from weight and colour, not from capitals. The league's
        # own name is left alone - that is what it is called.
        body = self.build()
        self.assertNotIn(">ADD<", body)
        self.assertNotIn(">DROP<", body)
        self.assertIn(">Add</span>", body)
        self.assertIn(">Drop</span>", body)

    def test_the_budget_line_can_be_recomputed_in_the_browser(self):
        body = self.build()
        self.assertIn("data-budget='100'", body)
        self.assertIn("data-cost", body)
        self.assertIn("data-league='1'", body)

    def test_an_old_reason_written_with_markup_is_not_shown_raw(self):
        body = self.build(drop_options=[],
                          rationale="RB &middot; 5 rostered")
        self.assertNotIn("&amp;middot;", body)
        self.assertIn("\u00b7", body)


class Staleness(unittest.TestCase):
    """Telling a page that is wrong from a page that is merely old."""

    def build(self, days_old, options=2):
        conn = st.connect(":memory:")
        run = st.start_run(conn, "2026", 1, ["ESPN"])
        opts = [{"id": f"d{i}", "name": f"Player {i} (GB RB)",
                 "position": "RB", "why": "weakest"} for i in range(options)]
        st.add_proposal(conn, run, league_id="1", league_name="LEHG",
                        add_player_id="a", add_player_name="Add Him (SF RB)",
                        add_position="RB", drop_player_id="d0",
                        drop_player_name="Player 0 (GB RB)",
                        drop_position="RB", bid=4, max_bid=100, consensus=1,
                        sources=[], rationale="", quote="",
                        drop_options=opts)
        when = (datetime.now(timezone.utc)
                - timedelta(days=days_old)).isoformat(timespec="seconds")
        conn.execute("UPDATE runs SET created_at = ? WHERE id = ?", (when, run))
        conn.commit()
        return conn

    def test_a_fortnight_old_run_says_so(self):
        body = webapp.render(self.build(13)).decode()
        self.assertIn("These proposals are 13 days old", body)

    def test_a_run_from_this_morning_does_not(self):
        body = webapp.render(self.build(0)).decode()
        self.assertNotIn("days old", body)

    def test_the_status_reports_what_is_on_the_page(self):
        info = webapp.run_summary(self.build(13, options=3))
        self.assertEqual(info["week"], 1)
        self.assertEqual(info["proposals"], 1)
        self.assertEqual(info["drop_options"], {"fewest": 3, "most": 3})
        self.assertEqual(info["statuses"], {st.PENDING: 1})

    def test_the_status_of_an_empty_page_does_not_explode(self):
        info = webapp.run_summary(st.connect(":memory:"))
        self.assertIsNone(info["run"])

    def test_a_refresh_failure_is_reported_until_one_succeeds(self):
        conn = self.build(1)
        st.log(conn, "refresh_failed", "TimeoutError: espn.com took too long")
        conn.commit()
        self.assertIn("took too long", webapp.render(conn).decode())
        self.assertIsNotNone(webapp.run_summary(conn)["last_refresh_failure"])
        st.log(conn, "refresh_ok", "")
        conn.commit()
        self.assertNotIn("took too long", webapp.render(conn).decode())
        self.assertIsNone(webapp.run_summary(conn)["last_refresh_failure"])


class Submitting(unittest.TestCase):
    """What the panel says after the Mac has had a go."""

    def build(self):
        conn = st.connect(":memory:")
        run = st.start_run(conn, "2026", 3, ["ESPN"])
        pid = st.add_proposal(conn, run, league_id="1", league_name="LEHG",
                              add_player_id="a",
                              add_player_name="Add Him (SF RB)",
                              add_position="RB", drop_player_id="d",
                              drop_player_name="Cut Him (GB RB)",
                              drop_position="RB", bid=4, max_bid=100,
                              consensus=1, sources=[], rationale="", quote="",
                              drop_options=[])
        st.decide(conn, pid, st.APPROVED, bid=4)
        return conn

    def test_before_anything_happens_it_says_what_is_needed(self):
        conn = self.build()
        st.ask_to_submit(conn)
        body = webapp.render(conn).decode()
        self.assertIn("signed in to Sleeper", body)

    def test_a_finished_attempt_with_the_claims_still_here_is_a_failure(self):
        # The claims are approved and unplaced and the attempt is over, so
        # whatever it reported, it did not work.
        conn = self.build()
        req = st.ask_to_submit(conn)
        st.claim_submit_request(conn, req)
        st.finish_submit_request(conn, req,
                                 "you are not signed in to Sleeper")
        body = webapp.render(conn).decode()
        self.assertIn("did not place them", body)
        self.assertIn("not signed in to Sleeper", body)

    def test_a_run_in_progress_says_so(self):
        conn = self.build()
        req = st.ask_to_submit(conn)
        st.claim_submit_request(conn, req)
        self.assertIn("placing them now", webapp.render(conn).decode())


class Consensus(unittest.TestCase):
    """How many analysts, as opposed to how many pages."""

    def merged(self, *urls):
        return ex.merge_sources({u: {"p1": {"faab": 5, "context": "x"}}
                                 for u in urls})["p1"]

    def test_two_articles_from_one_site_are_one_analyst(self):
        got = self.merged("https://www.rotoballer.com/a",
                          "https://rotoballer.com/b")
        self.assertEqual(got["count"], 1)

    def test_different_sites_still_count_separately(self):
        got = self.merged("https://www.rotoballer.com/a",
                          "https://www.draftsharks.com/b",
                          "https://www.espn.com/c")
        self.assertEqual(got["count"], 3)

    def test_every_page_is_still_kept_for_showing(self):
        got = self.merged("https://www.rotoballer.com/a",
                          "https://rotoballer.com/b")
        self.assertEqual(len(got["sources"]), 2)

    def test_www_and_a_port_do_not_make_a_new_publication(self):
        self.assertEqual(ex.publication("https://www.espn.com:443/x"),
                         "espn.com")

    def test_the_card_does_not_name_a_site_twice(self):
        conn = st.connect(":memory:")
        run = st.start_run(conn, "2026", 2, [])
        st.add_proposal(conn, run, league_id="1", league_name="LEHG",
                        add_player_id="a", add_player_name="Add Him (SF RB)",
                        add_position="RB", drop_player_id="d",
                        drop_player_name="Cut Him (GB RB)",
                        drop_position="RB", bid=4, max_bid=100, consensus=2,
                        sources=["https://www.rotoballer.com/a",
                                 "https://rotoballer.com/b",
                                 "https://www.draftsharks.com/c"],
                        rationale="", quote="", drop_options=[])
        body = webapp.render(conn).decode().split("</style></head>")[1]
        self.assertIn("rotoballer.com, draftsharks.com", body)
        self.assertNotIn("rotoballer.com, rotoballer.com", body)


class QuietLeagues(unittest.TestCase):
    """A league that produced nothing says why it produced nothing."""

    def build(self, note):
        conn = st.connect(":memory:")
        st.start_run(conn, "2026", 2, ["ESPN"], note=note)
        return webapp.render(conn).decode().split("</style></head>")[1]

    def test_the_reason_is_shown_not_the_absence(self):
        body = self.build(json.dumps(
            {"OTG Alumni": "the best player available is not worth more than "
                           "the weakest player you would have to drop"}))
        self.assertIn("OTG Alumni", body)
        self.assertIn("not worth more than", body)
        self.assertIn("Nothing to do in 1 league", body)

    def test_several_leagues_are_listed(self):
        body = self.build(json.dumps({"OTG Alumni": "one", "LEHG": "two"}))
        self.assertIn("Nothing to do in 2 leagues", body)

    def test_a_run_with_no_note_says_nothing_extra(self):
        self.assertNotIn("Nothing to do in", self.build(""))

    def test_a_note_that_is_not_json_is_ignored_not_fatal(self):
        self.assertNotIn("Nothing to do in", self.build("just some text"))


class NoBench(unittest.TestCase):
    """A league whose bench is unavailable still gets a recommendation.

    The gate deciding whether to propose anything looked only at the bench,
    while the card underneath offered the whole roster - so a bench that was
    full, or entirely never-drops, silently produced nothing for a league
    that would happily have cut a starter for a better player.
    """

    PLAYERS = {
        "s1": {"full_name": "My Starter", "position": "RB", "team": "GB",
               "active": True, "status": "Active", "fantasy_positions": ["RB"]},
        "s2": {"full_name": "Second Starter", "position": "WR", "team": "NYJ",
               "active": True, "status": "Active", "fantasy_positions": ["WR"]},
        "free": {"full_name": "Kaelon Black", "position": "RB", "team": "SF",
                 "active": True, "status": "Active",
                 "fantasy_positions": ["RB"]},
    }
    ROSTER = {"roster_id": 1, "owner_id": "me", "starters": ["s1", "s2"],
              "players": ["s1", "s2"], "settings": {"waiver_budget_used": 0}}
    LEAGUE = {"league_id": "L1", "name": "OTG Alumni",
              "settings": {"num_teams": 10, "waiver_budget": 100},
              "scoring_settings": {"rec": 0.5},
              "roster_positions": ["RB", "WR"]}
    ARTICLE = ("Kaelon Black, RB, 49ers: Black is the top waiver add this "
               "week. Spend 4% of your FAAB budget to add him.")

    def run_it(self):
        import run_weekly as rw
        import sleeper_client as sc
        real = sc.league_rosters
        sc.league_rosters = lambda _lid: [self.ROSTER]
        try:
            return rw.proposals_for_league(
                self.LEAGUE, "me", self.PLAYERS, {},
                {"https://espn.com/x": self.ARTICLE}, 3)
        finally:
            sc.league_rosters = real

    def test_a_roster_with_no_bench_still_proposes(self):
        rows, why = self.run_it()
        self.assertEqual(len(rows), 1, why)
        self.assertIn("Kaelon Black", rows[0]["add_player_name"])

    def test_the_drop_it_picks_is_on_the_roster(self):
        rows, _why = self.run_it()
        self.assertIn(rows[0]["drop_player_id"], ("s1", "s2"))

    def test_and_the_card_still_offers_the_alternatives(self):
        rows, _why = self.run_it()
        self.assertGreaterEqual(len(rows[0]["drop_options"]), 2)


class DraftCost(unittest.TestCase):
    """Where you took somebody, as a reason to think twice about cutting him."""

    def test_an_early_pick_is_stickier_than_a_late_one(self):
        self.assertGreater(ew.draft_weight({"round": 1}, 1),
                           ew.draft_weight({"round": 12}, 1))

    def test_a_big_auction_buy_is_stickier_than_a_dollar_flier(self):
        self.assertGreater(ew.draft_weight({"amount": 45}, 1),
                           ew.draft_weight({"amount": 1}, 1))

    def test_an_undrafted_player_gets_nothing_either_way(self):
        self.assertEqual(ew.draft_weight(None, 1), 0.0)
        self.assertEqual(ew.draft_weight({}, 1), 0.0)

    def test_it_fades_as_the_season_goes_on(self):
        # By November there are ten games of evidence and the draft is the
        # sunk cost it always was.
        early = ew.draft_weight({"round": 2}, 1)
        late = ew.draft_weight({"round": 2}, 14)
        self.assertLess(late, early / 2)

    def test_it_never_fades_to_nothing(self):
        self.assertGreater(ew.draft_weight({"round": 2}, 30), 0)

    def test_it_moves_people_around_without_deciding_everything(self):
        # Two players of the same worth, one of them a second-round pick.
        players = {
            "cheap": {"full_name": "Cheap Man", "position": "RB",
                      "search_rank": 300},
            "dear": {"full_name": "Dear Man", "position": "RB",
                     "search_rank": 300},
        }
        roster = {"starters": [], "players": ["cheap", "dear"]}
        depth = {"RB": (2, 1.0, "deep")}
        ranked = ew.drop_candidates(roster, players, {}, depth,
                                    cost={"dear": {"round": 2}}, week=1)
        self.assertEqual(ranked[0][2], "cheap")


class BenchIsNotAVerdict(unittest.TestCase):
    """Being out of the lineup says less than it used to.

    Starting was worth more than every other signal combined, so any bench
    player was offered before any starter. People sit out weeks for reasons
    that say nothing about their value - a bye being the obvious one - and a
    star resting outranked every scrub on the roster.
    """

    PLAYERS = {
        "scrub": {"full_name": "Bench Scrub", "position": "RB",
                  "search_rank": 600},
        "weak": {"full_name": "Weak Starter", "position": "RB",
                 "search_rank": 400},
        "star": {"full_name": "Star On Bye", "position": "RB",
                 "search_rank": 12},
    }
    ROSTER = {"starters": ["weak"], "players": ["scrub", "weak", "star"]}
    DEPTH = {"RB": (3, 1.0, "deep")}

    def order(self):
        return [r[2] for r in ew.drop_candidates(
            self.ROSTER, self.PLAYERS, {}, self.DEPTH)]

    def test_the_worst_player_is_still_offered_first(self):
        self.assertEqual(self.order()[0], "scrub")

    def test_a_weak_starter_is_offered_before_a_benched_star(self):
        got = self.order()
        self.assertLess(got.index("weak"), got.index("star"))

    def test_starting_still_counts_for_something(self):
        # Same player twice, one of them in the lineup.
        players = {"a": dict(self.PLAYERS["weak"]),
                   "b": dict(self.PLAYERS["weak"])}
        roster = {"starters": ["a"], "players": ["a", "b"]}
        ranked = ew.drop_candidates(roster, players, {}, self.DEPTH)
        self.assertEqual(ranked[0][2], "b")

    def test_a_bye_is_named_rather_than_left_to_look_like_a_benching(self):
        why = rw.drop_reason({"position": "RB"}, "deep",
                             {"RB": (5, 2, "deep")}, False, 4, 5, on_bye=True)
        self.assertIn("on bye this week", why)

    def test_a_starter_is_never_also_called_on_bye(self):
        why = rw.drop_reason({"position": "RB"}, "deep",
                             {"RB": (5, 2, "deep")}, True, 4, 5, on_bye=True)
        self.assertIn("in your lineup", why)
        self.assertNotIn("bye", why)

    def test_the_card_says_what_you_paid(self):
        self.assertIn("drafted in round 6",
                      rw.drop_reason({"position": "RB"}, "deep",
                                     {"RB": (5, 2, "deep")}, False, 2, 5,
                                     cost={"round": 6}))
        self.assertIn("you paid $24 for him",
                      rw.drop_reason({"position": "RB"}, "deep",
                                     {"RB": (5, 2, "deep")}, False, 2, 5,
                                     cost={"amount": 24}))

    def test_an_undrafted_player_says_nothing_about_a_draft(self):
        why = rw.drop_reason({"position": "RB"}, "deep",
                             {"RB": (5, 2, "deep")}, False, 2, 5)
        self.assertNotIn("draft", why)
        self.assertNotIn("paid", why)


class WhenNobodyWroteAboutHim(unittest.TestCase):
    """The add pool used to be whatever the week's articles named.

    That is the right default and it fails in one direction. When the
    articles cover positions you are set at, or name only streamers, the
    best player genuinely free in your league is never considered - and the
    card offers a deep streaming option as though nothing better existed.
    """

    LEAGUE = {"league_id": "L", "name": "OTG",
              "roster_positions": ["WR", "WR", "BN", "BN"]}
    MINE = {"starters": ["mine1"], "players": ["mine1", "junk"]}
    PLAYERS = {
        "mine1": {"full_name": "My Starter", "position": "WR",
                  "search_rank": 120},
        "junk": {"full_name": "Roster Filler", "position": "WR",
                 "search_rank": 700},
        "streamer": {"full_name": "Deep Streamer", "position": "TE",
                     "search_rank": 480, "team": "X"},
        "stud": {"full_name": "Actually Good", "position": "WR",
                 "search_rank": 38, "depth_chart_order": 1, "team": "Y"},
    }
    DEPTH = {"WR": (2, 1.0, "deep"), "TE": (0, 0, "thin")}

    def ask(self, available, beat_id=None, remaining=89):
        beat = (wa.score_player(self.PLAYERS[beat_id], 0) if beat_id else 0.0)
        return rw.best_available(self.LEAGUE, self.MINE, self.PLAYERS, [],
                                 available, {}, self.DEPTH, {}, 3,
                                 remaining, beat=beat)

    def test_a_clearly_better_free_agent_is_offered(self):
        got = self.ask(["streamer", "stud"], beat_id="streamer")
        self.assertIsNotNone(got)
        self.assertEqual(got["add_player_id"], "stud")

    def test_it_stays_quiet_when_the_articles_had_the_best_man(self):
        self.assertIsNone(self.ask(["streamer"], beat_id="streamer"))

    def test_a_marginal_edge_is_not_enough(self):
        """The analysts are the point of this tool; ties go to them."""
        near = dict(self.PLAYERS["streamer"], search_rank=460)
        players = dict(self.PLAYERS, near=near)
        got = rw.best_available(self.LEAGUE, self.MINE, players, [],
                                ["near"], {}, self.DEPTH, {}, 3, 89,
                                beat=wa.score_player(self.PLAYERS["streamer"], 0))
        self.assertIsNone(got)

    def test_it_says_nobody_wrote_about_him_rather_than_faking_a_source(self):
        got = self.ask(["streamer", "stud"], beat_id="streamer")
        self.assertEqual(got["consensus"], 0)
        self.assertEqual(got["sources"], [])
        self.assertEqual(got["quote"], "")

    def test_it_still_names_somebody_to_drop(self):
        got = self.ask(["streamer", "stud"], beat_id="streamer")
        self.assertEqual(got["drop_player_id"], "junk")
        self.assertTrue(got["drop_options"])

    def test_it_will_not_cut_someone_better_than_the_man_it_adds(self):
        lean = {"stud": self.PLAYERS["stud"],
                "mine1": {"full_name": "Better Than Him", "position": "WR",
                          "search_rank": 5, "depth_chart_order": 1}}
        mine = {"starters": [], "players": ["mine1"]}
        self.assertIsNone(
            rw.best_available(self.LEAGUE, mine, lean, [], ["stud"], {},
                              self.DEPTH, {}, 3, 89))

    def test_with_no_roster_left_to_cut_it_offers_nothing(self):
        mine = {"starters": [], "players": []}
        self.assertIsNone(
            rw.best_available(self.LEAGUE, mine, self.PLAYERS, [], ["stud"],
                              {}, self.DEPTH, {}, 3, 89))

    def test_the_bid_stays_a_single_dollar(self):
        """Nobody wrote him up, so there is no analyst FAAB to anchor to."""
        got = self.ask(["streamer", "stud"], beat_id="streamer")
        self.assertEqual(got["bid"], 1)
        self.assertIsNone(got["bid_low"])

    def test_a_league_with_no_budget_bids_nothing(self):
        got = self.ask(["streamer", "stud"], beat_id="streamer", remaining=0)
        self.assertIsNone(got["bid"])


class BeingHurtIsNotBeingBad(unittest.TestCase):
    """A one-week injury is not a reason to cut somebody.

    The model scored every player once and used that one number for both
    "who should I pick up" and "who should I let go". Being out multiplies
    a score by 0.25, which is right for the first question and ruinous for
    the second: a star receiver out for a single week landed below every
    healthy bench body on the roster and got offered up as the drop, with
    "out" as the leading argument for cutting him.
    """

    STAR = {"full_name": "Star Receiver", "position": "WR",
            "search_rank": 45, "depth_chart_order": 1,
            "injury_status": "Out"}
    SCRUB = {"full_name": "Deep Bench", "position": "WR",
             "search_rank": 420, "depth_chart_order": 2,
             "injury_status": ""}

    def test_a_star_out_for_a_week_is_worth_more_than_a_healthy_scrub(self):
        self.assertGreater(wa.keep_value(self.STAR, 0),
                           wa.keep_value(self.SCRUB, 0))

    def test_and_the_old_scoring_had_it_the_other_way_round(self):
        """The regression, stated as such, so it cannot come back quietly."""
        self.assertLess(wa.score_player(self.STAR, 0),
                        wa.score_player(self.SCRUB, 0))

    def test_adding_him_this_week_is_still_discounted(self):
        """Being out matters for whether he helps you on Sunday."""
        healthy = dict(self.STAR, injury_status="")
        self.assertLess(wa.score_player(self.STAR, 0),
                        wa.score_player(healthy, 0))

    def test_a_season_ending_status_still_makes_him_droppable(self):
        for status in ("IR", "PUP", "Sus", "NFI"):
            with self.subTest(status=status):
                gone = dict(self.STAR, injury_status=status)
                self.assertLess(wa.keep_value(gone, 0),
                                wa.keep_value(self.SCRUB, 0))

    def test_questionable_and_doubtful_are_this_week_too(self):
        for status in ("Questionable", "Doubtful"):
            with self.subTest(status=status):
                iffy = dict(self.STAR, injury_status=status)
                self.assertEqual(wa.keep_value(iffy, 0),
                                 wa.keep_value(dict(self.STAR,
                                                    injury_status=""), 0))

    def test_the_scrub_is_the_one_offered_for_the_drop(self):
        players = {"star": self.STAR, "scrub": self.SCRUB}
        roster = {"starters": [], "players": ["star", "scrub"]}
        order = [r[2] for r in ew.drop_candidates(
            roster, players, {}, {"WR": (2, 1.0, "deep")})]
        self.assertEqual(order[0], "scrub")

    def test_the_card_does_not_argue_from_a_one_week_injury(self):
        why = rw.drop_reason(self.STAR, "deep", {"WR": (7, 3, "deep")},
                             False, 2, 7)
        self.assertNotIn("out", why.lower().split(", ")[0])

    def test_but_it_does_say_so_when_he_is_gone_for_the_season(self):
        why = rw.drop_reason(dict(self.STAR, injury_status="IR"), "deep",
                             {"WR": (7, 3, "deep")}, False, 2, 7)
        self.assertTrue(why.lower().startswith("ir"), why)


class SaidToDrop(unittest.TestCase):
    """Analysts naming one of yours as finished - the counterweight."""

    PLAYERS = {"1": {"full_name": "J.K. Dobbins"},
               "2": {"full_name": "Kaelon Black"},
               "3": {"full_name": "Alec Pierce"}}

    def gaz(self):
        return ex.build_gazetteer(self.PLAYERS, ["1", "2", "3"])

    def test_a_plain_drop_call_is_found(self):
        got = ex.extract_drops(
            "J.K. Dobbins is droppable in all formats after losing the job.",
            self.gaz())
        self.assertEqual(sorted(got), ["1"])

    def test_initials_do_not_cut_the_sentence_short(self):
        # The full stop in "J.K." was ending the sentence, leaving the drop
        # cue outside it and nothing to find.
        lo, hi = ex._sentence_bounds("J.K. Dobbins is droppable this week.", 0)
        self.assertIn("droppable", "J.K. Dobbins is droppable this week."[lo:hi])

    def test_a_suffix_does_not_either(self):
        text = "Brian Robinson Jr. is worth a look this week."
        lo, hi = ex._sentence_bounds(text, 0)
        self.assertEqual(text[lo:hi], text)

    def test_the_man_being_added_in_a_swap_is_not_marked(self):
        got = ex.extract_drops(
            "Add Kaelon Black this week; you can drop Alec Pierce for him.",
            self.gaz())
        self.assertEqual(sorted(got), ["3"])

    def test_merely_being_mentioned_is_not_a_drop_call(self):
        self.assertEqual(ex.extract_drops(
            "Alec Pierce had a quiet game but remains a hold.", self.gaz()), {})

    def test_two_articles_from_one_site_are_one_analyst(self):
        merged = ex.merge_drops({
            "https://www.rotoballer.com/a": {"1": "Drop him."},
            "https://rotoballer.com/b": {"1": "Cut him."},
        })
        self.assertEqual(merged["1"]["count"], 1)

    def test_the_card_says_who_said_it(self):
        self.assertIn("2 analysts say to let him go",
                      rw.drop_reason({"position": "RB"}, "deep",
                                     {"RB": (5, 2, "deep")}, False, 2, 5,
                                     advice={"count": 2}))
        self.assertIn("1 analyst says to let him go",
                      rw.drop_reason({"position": "RB"}, "deep",
                                     {"RB": (5, 2, "deep")}, False, 2, 5,
                                     advice={"count": 1}))

    def test_both_halves_appear_together(self):
        # The whole point: what you paid, and somebody saying it no longer
        # matters, in one line you can weigh.
        why = rw.drop_reason({"position": "RB"}, "deep",
                             {"RB": (5, 2, "deep")}, False, 2, 5,
                             cost={"round": 6}, advice={"count": 2})
        self.assertIn("drafted in round 6", why)
        self.assertIn("analysts say to let him go", why)


class Candidates(unittest.TestCase):
    """Who is offered as a drop."""

    PLAYERS = {
        "s1": {"full_name": "My Starter", "position": "RB", "team": "GB"},
        "s2": {"full_name": "Untouchable", "position": "QB", "team": "CHI"},
        "b1": {"full_name": "Bench One", "position": "WR", "team": "NYJ"},
        "b2": {"full_name": "Bench Two", "position": "WR", "team": "LV"},
    }
    ROSTER = {"starters": ["s1", "s2"], "players": ["s1", "s2", "b1", "b2"]}
    DEPTH = {"RB": (1, 2.3, "thin"), "QB": (1, 1.0, "ok"),
             "WR": (2, 2.3, "deep")}

    def candidates(self, protect=None):
        import expert_waivers as ew
        import waiver_analyzer as wa
        real = wa.never_drop_names
        wa.never_drop_names = lambda: protect or set()
        try:
            return ew.drop_candidates(self.ROSTER, self.PLAYERS, {}, self.DEPTH)
        finally:
            wa.never_drop_names = real

    def test_starters_are_offered_too(self):
        # Dropping a starter is normal when the man you are adding is
        # better than him.
        names = [p["full_name"] for _r, _s, _p, p, _l, _st in self.candidates()]
        self.assertIn("My Starter", names)
        self.assertIn("Bench One", names)
        self.assertEqual(len(names), 4)

    def test_bench_players_come_before_starters(self):
        rows = self.candidates()
        starting = [row[5] for row in rows]
        self.assertEqual(starting, sorted(starting))

    def test_a_starter_is_marked_as_one(self):
        rows = {row[2]: row[5] for row in self.candidates()}
        self.assertTrue(rows["s1"])
        self.assertFalse(rows["b1"])

    def test_the_never_drop_list_is_the_one_exclusion(self):
        names = [p["full_name"] for _r, _s, _p, p, _l, _st
                 in self.candidates(protect={"untouchable"})]
        self.assertNotIn("Untouchable", names)
        self.assertEqual(len(names), 3)


class Starters(unittest.TestCase):
    """Dropping a starter is offered, so it must also be allowed through."""

    ROSTERS = [{"owner_id": "me", "roster_id": 1, "starters": ["s1"],
                "players": ["s1", "b1"],
                "settings": {"waiver_budget_used": 0}}]

    def setUp(self):
        import sleeper_client as sc
        self.real = sc.league_rosters
        sc.league_rosters = lambda _lid: self.ROSTERS

    def tearDown(self):
        import sleeper_client as sc
        sc.league_rosters = self.real

    def preflight(self, drop):
        import claim_safety as cs
        return cs.preflight({"league_id": "1", "add_player_id": "new",
                             "add_player_name": "Kaelon Black (IND RB)",
                             "drop_player_id": drop, "drop_player_name": drop,
                             "bid": 3, "max_bid": 100}, "me")

    def test_a_starter_you_chose_is_not_refused(self):
        ok, why, _settled = self.preflight("s1")
        self.assertTrue(ok)
        self.assertIn("as approved", why)

    def test_a_bench_drop_says_nothing_extra(self):
        self.assertEqual(self.preflight("b1"), (True, "ok", False))

    def test_somebody_who_left_your_roster_is_still_refused(self):
        ok, why, settled = self.preflight("gone")
        self.assertFalse(ok)
        self.assertIn("no longer on your roster", why)
        # You can pick a different drop, so this one is not finished.
        self.assertFalse(settled)

    def test_a_player_another_team_took_is_finished_not_waiting(self):
        import claim_safety as cs
        import sleeper_client as sc
        rosters = [dict(self.ROSTERS[0]),
                   {"owner_id": "them", "roster_id": 2, "starters": [],
                    "players": ["new"], "settings": {}}]
        real = sc.league_rosters
        sc.league_rosters = lambda _lid: rosters
        try:
            ok, why, settled = cs.preflight(
                {"league_id": "1", "add_player_id": "new",
                 "add_player_name": "Kaelon Black (IND RB)",
                 "drop_player_id": "b1", "drop_player_name": "b1",
                 "bid": 3, "max_bid": 100}, "me")
        finally:
            sc.league_rosters = real
        self.assertFalse(ok)
        self.assertIn("another team has him", why)
        self.assertTrue(settled)


if __name__ == "__main__":
    unittest.main(verbosity=2)
