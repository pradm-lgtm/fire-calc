#!/usr/bin/env python3
"""Tests for which week the agent plans for.

    python3 test_week.py

Sleeper carries two week numbers and they disagree all Monday. Reading the
wrong one sent the waiver job hunting for last week's articles while the
analysts had already published this week's.
"""

import unittest

import nfl_week
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


class GameDay(unittest.TestCase):
    """Which day a kickoff belongs to, in the time zone the schedule uses.

    Kickoffs are UTC and a Thursday night game is Friday there, so taking
    the UTC weekday would put every prime-time game on the following day -
    including the one the whole Thursday feature is about.
    """

    def at(self, year, month, day, hour, minute=0):
        from datetime import datetime, timezone
        return datetime(year, month, day, hour, minute,
                        tzinfo=timezone.utc).timestamp()

    def test_thursday_night_is_thursday_not_friday(self):
        # 8:15pm eastern on Thursday is 00:15 UTC on Friday.
        self.assertEqual(nfl_week.game_day(self.at(2026, 9, 18, 0, 15)), "Thu")

    def test_the_sunday_afternoon_window(self):
        self.assertEqual(nfl_week.game_day(self.at(2026, 9, 20, 17, 0)), "Sun")

    def test_sunday_night_is_still_sunday(self):
        self.assertEqual(nfl_week.game_day(self.at(2026, 9, 21, 0, 20)), "Sun")

    def test_monday_night_is_monday(self):
        self.assertEqual(nfl_week.game_day(self.at(2026, 9, 22, 0, 15)), "Mon")

    def test_a_london_morning_is_not_the_day_before(self):
        # 9:30am eastern, the earliest kickoff there is.
        self.assertEqual(nfl_week.game_day(self.at(2026, 9, 20, 13, 30)), "Sun")

    def test_an_unknown_kickoff_has_no_day(self):
        self.assertIsNone(nfl_week.game_day(None))
        self.assertIsNone(nfl_week.game_day(0))
        self.assertIsNone(nfl_week.game_day("not a time"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
