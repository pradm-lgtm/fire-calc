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


if __name__ == "__main__":
    unittest.main(verbosity=2)
