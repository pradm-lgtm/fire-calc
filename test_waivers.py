#!/usr/bin/env python3
"""Tests for the waiver review page and the reasons it shows.

    python3 test_waivers.py

The quote is the part worth guarding. A ranking page wraps its tables in
furniture that names a dozen players and analyses none of them, and one of
those arrived on a card as the reason to spend real FAAB.
"""

import re
import unittest

import expert_extract as ex
import store as st
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
        self.assertIn("No write-up found for him", self.build())

    def test_a_real_write_up_is_shown(self):
        self.assertIn("eight targets", self.build({"quote": WRITEUP}))

    def test_the_drop_reason_stays_short(self):
        self.assertIn("WR is deep", self.build())

    def test_the_button_names_the_number_beside_it(self):
        self.assertIn("Approve at 6", self.build())

    def test_the_analyst_range_is_shown(self):
        self.assertIn("Analysts bid 3 to 11", self.build())

    def test_no_analyst_number_is_admitted_rather_than_invented(self):
        html = self.build({"bid_low": None, "bid_high": None})
        self.assertIn("No analyst put a number on him", html)

    def test_the_budget_line_counts_every_pending_bid(self):
        # The number you need before approving three bids in one league is
        # what is left after all three, not what is left now.
        html = self.build({"bid": 6}, {"bid": 14}, {"bid": 4})
        bar = re.search(r"class='bar'>(.*?)</div>", html).group(1)
        self.assertIn("approving all 3 costs 24", bar)
        self.assertIn("leaving <strong>76</strong>", bar)

    def test_bids_beyond_the_budget_are_called_out(self):
        html = self.build({"bid": 60, "max_bid": 100},
                          {"bid": 70, "max_bid": 100})
        self.assertIn("more than you have", html)

    def test_the_refresh_reads_as_a_refresh(self):
        html = self.build()
        self.assertIn("Re-check waivers", html)
        self.assertNotIn("Work them out again", html)


OPTIONS = [{"id": "d1", "name": "J.K. Dobbins (DEN RB)", "position": "RB",
            "why": "RB &middot; 5 rostered, more than you start"},
           {"id": "d2", "name": "Alec Pierce (IND WR)", "position": "WR",
            "why": "WR &middot; 7 rostered, more than you start"}]


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
                       sources=["https://espn.com/x"], rationale="RB is deep",
                       drop_options=OPTIONS)
            row.update(over)
            st.add_proposal(conn, run, **row)
        return conn

    def test_the_drop_is_a_choice_with_reasons_beside_it(self):
        html = webapp.render(self.build()).decode()
        self.assertIn("<select name='drop'>", html)
        self.assertIn("Alec Pierce", html)
        self.assertIn("more than you start", html)

    def test_the_suggested_drop_is_preselected(self):
        html = webapp.render(self.build()).decode()
        self.assertIn("<option value='d1' selected>", html)

    def test_choosing_a_different_drop_is_recorded(self):
        conn = self.build()
        row = st.proposals_for_run(conn, st.latest_run(conn)["id"])[0]
        st.decide(conn, row["id"], st.APPROVED, bid=5,
                  drop_player_id="d2",
                  drop_player_name=webapp.drop_name(conn, row["id"], "d2"))
        after = st.proposals_for_run(conn, st.latest_run(conn)["id"])[0]
        self.assertEqual(after["drop_player_id"], "d2")
        self.assertEqual(after["drop_player_name"], "Alec Pierce (IND WR)")

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
        self.assertIn("is a fallback that only runs if the claim above it "
                      "fails", body)
        self.assertIn("Fallback", body)

    def test_distinct_drops_are_not_flagged(self):
        body = self.body({}, {"drop_player_id": "d2",
                              "drop_player_name": "Alec Pierce (IND WR)"})
        self.assertNotIn("fallback that only runs", body)
        self.assertNotIn("Fallback", body)


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
        ok, why = self.preflight("s1")
        self.assertTrue(ok)
        self.assertIn("as approved", why)

    def test_a_bench_drop_says_nothing_extra(self):
        self.assertEqual(self.preflight("b1"), (True, "ok"))

    def test_somebody_who_left_your_roster_is_still_refused(self):
        ok, why = self.preflight("gone")
        self.assertFalse(ok)
        self.assertIn("no longer on your roster", why)


if __name__ == "__main__":
    unittest.main(verbosity=2)
