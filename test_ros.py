#!/usr/bin/env python3
"""Rest-of-season rankings, which are the question a drop actually asks.

Everything the model knew about worth was a weekly projection or
Sleeper's search_rank. Neither answers "should I still own him" - one is
about a single Sunday and the other is how often a name gets looked up -
so good players were offered as the drop while finished ones sat on the
roster untouched.
"""

import unittest

import ros
import waiver_analyzer as wa


class TurningARankIntoAWorth(unittest.TestCase):

    def test_the_best_player_is_worth_the_most(self):
        self.assertEqual(ros.value(1), 100.0)

    def test_it_falls_away_with_rank(self):
        values = [ros.value(r) for r in (1, 5, 24, 60, 150)]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_the_top_of_the_list_is_spread_out_more_than_the_bottom(self):
        """Third versus tenth matters; two hundredth versus two hundred
        and seventh does not."""
        near_top = ros.value(3) - ros.value(10)
        near_bottom = ros.value(200) - ros.value(207)
        self.assertGreater(near_top, near_bottom * 3)

    def test_past_the_end_of_the_list_is_nothing(self):
        self.assertEqual(ros.value(ros.FLOOR + 50), 0.0)

    def test_nonsense_is_none_rather_than_a_number(self):
        for bad in (None, 0, -3, "twelve"):
            self.assertIsNone(ros.value(bad))


class NotOnTheListAtAll(unittest.TestCase):
    """The half that makes the obviously finished players droppable.

    A man missing from a three-hundred-deep list is not missing data. He
    is somebody no analyst would hold for the rest of the year, and that
    is the most useful thing the list says about him.
    """

    RANKS = {"good": 20.0, "ok": 120.0}

    def test_a_ranked_player_is_worth_his_rank(self):
        self.assertAlmostEqual(ros.worth(self.RANKS, "good"), ros.value(20.0))

    def test_an_unranked_player_is_worth_nothing_not_unknown(self):
        self.assertEqual(ros.worth(self.RANKS, "nobody"), 0.0)

    def test_which_is_the_whole_point(self):
        """Returning None let him keep a score built on a weekly
        projection and a search rank, so he was never offered."""
        finished = {"search_rank": 140, "depth_chart_order": 3,
                    "position": "WR"}
        as_unknown = wa.keep_value(finished, 0, 7.0, None)
        as_unranked = wa.keep_value(finished, 0, 7.0,
                                    ros.worth(self.RANKS, "nobody"))
        self.assertLess(as_unranked, as_unknown / 2)

    def test_no_rankings_at_all_is_a_real_unknown(self):
        """The page would not load, which is not a verdict on anybody."""
        self.assertIsNone(ros.worth({}, "good"))
        self.assertIsNone(ros.worth(None, "good"))

    def test_ids_are_compared_as_strings(self):
        self.assertGreater(ros.worth({"123": 10.0}, 123), 0)

    def test_it_names_who_is_unranked(self):
        self.assertEqual(ros.unranked(self.RANKS, ["good", "x", "y"]),
                         ["x", "y"])


class WhereItSitsInTheDecision(unittest.TestCase):

    GOOD = {"search_rank": 300, "depth_chart_order": 2, "position": "WR"}
    FINISHED = {"search_rank": 140, "depth_chart_order": 3, "position": "WR"}

    def test_a_well_ranked_player_outranks_an_unranked_one(self):
        """Even when Sleeper thinks better of the second.

        This is the Luther Burden complaint stated as a test: he was being
        offered while players no analyst would hold were not.
        """
        held = wa.keep_value(self.GOOD, 0, 7.0, ros.value(55))
        cut = wa.keep_value(self.FINISHED, 0, 7.0, 0.0)
        self.assertGreater(held, cut)

    def test_and_sleeper_alone_had_it_the_other_way_round(self):
        self.assertGreater(wa.keep_value(self.FINISHED, 0, 7.0),
                           wa.keep_value(self.GOOD, 0, 7.0))

    def test_without_rankings_nothing_changes(self):
        self.assertEqual(wa.keep_value(self.GOOD, 0, 7.0, None),
                         wa.keep_value(self.GOOD, 0, 7.0))

    def test_the_rank_leads_but_does_not_decide_alone(self):
        """A projection still moves him, so one stale list cannot govern."""
        lean = wa.keep_value(self.GOOD, 0, 3.0, ros.value(55))
        fat = wa.keep_value(self.GOOD, 0, 20.0, ros.value(55))
        self.assertGreater(fat, lean)


class FetchingIsNotFatal(unittest.TestCase):

    def test_a_page_that_will_not_load_gives_nothing(self):
        import lineup
        real = lineup.gather_rankings

        def broken(*a, **k):
            raise RuntimeError("no network")
        lineup.gather_rankings = broken
        try:
            self.assertEqual(ros.fetch({}), {})
        finally:
            lineup.gather_rankings = real

    def test_it_asks_for_the_rest_of_season_list(self):
        url = ros.SOURCES[0]["url"]
        self.assertIn("ros-", url)
        self.assertIn("overall", url)


if __name__ == "__main__":
    unittest.main(verbosity=2)
