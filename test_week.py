#!/usr/bin/env python3
"""Tests for which week the agent plans for.

    python3 test_week.py

Sleeper carries two week numbers and they disagree all Monday. Reading the
wrong one sent the waiver job hunting for last week's articles while the
analysts had already published this week's.
"""

import unittest

import sleeper_client as sc


class WhichWeek(unittest.TestCase):

    def test_the_week_the_app_shows_wins(self):
        # `week` is the scoring week and only advances on Tuesday, so all
        # Monday it names the week whose games just finished.
        self.assertEqual(sc.current_week({"week": 1, "display_week": 2}), 2)

    def test_it_falls_back_when_only_one_is_given(self):
        self.assertEqual(sc.current_week({"week": 3}), 3)
        self.assertEqual(sc.current_week({"display_week": 4}), 4)

    def test_a_number_sent_as_text_still_counts(self):
        self.assertEqual(sc.current_week({"display_week": "4"}), 4)

    def test_zero_is_not_a_week(self):
        # The preseason reports 0, and it must not win over a real number.
        self.assertEqual(sc.current_week({"display_week": 0, "week": 5}), 5)

    def test_nothing_at_all_is_week_one(self):
        self.assertEqual(sc.current_week({}), 1)
        self.assertEqual(sc.current_week({"week": None}), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
