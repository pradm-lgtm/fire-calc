#!/usr/bin/env python3
"""Tests for the start/sit verdicts and the row shape they travel in.

    python3 test_lineup.py

The row shape is worth a test because it crosses three boundaries - the
terminal report, an HTTP post, and an INSERT - and a renamed key would only
surface on a Sunday morning.
"""

import unittest

import lineup
import rankings as rk
import store as st

PLAYERS = {
    "qb1": {"position": "QB", "full_name": "Dak Prescott", "team": "DAL",
            "fantasy_positions": ["QB"]},
    "qb2": {"position": "QB", "full_name": "Jayden Daniels", "team": "WAS",
            "fantasy_positions": ["QB"]},
    "rb1": {"position": "RB", "full_name": "J.K. Dobbins", "team": "DEN",
            "fantasy_positions": ["RB"]},
    "rb2": {"position": "RB", "full_name": "MarShawn Lloyd", "team": "GB",
            "fantasy_positions": ["RB"]},
}

LEAGUE = {"league_id": "77", "name": "OTG Alumni",
          "roster_positions": ["QB", "FLEX", "BN", "BN"]}

ROSTER = {"starters": ["qb1", "rb1"],
          "players": ["qb1", "rb1", "qb2", "rb2"]}


def consensus(qb1, qb2, rb1, rb2):
    return rk.merge({"page": {
        "QB": {"qb1": qb1, "qb2": qb2},
        rk.OVERALL: {"rb1": rb1, "rb2": rb2},
    }})


class Verdicts(unittest.TestCase):

    def rows(self, **kw):
        return lineup.flag_rows(LEAGUE, ROSTER, PLAYERS, consensus(**kw))

    def test_a_small_gap_is_green(self):
        rows = self.rows(qb1=10, qb2=9, rb1=60, rb2=59)
        self.assertEqual([r["verdict"] for r in rows], ["GREEN", "GREEN"])

    def test_a_wide_gap_is_red_and_names_the_alternative(self):
        rows = self.rows(qb1=40, qb2=2, rb1=60, rb2=59)
        self.assertEqual(rows[0]["verdict"], "RED")
        self.assertEqual(rows[0]["better_name"], "Jayden Daniels (WAS QB)")
        self.assertIn("consensus prefers Jayden Daniels", rows[0]["detail"])

    def test_a_starter_nobody_ranks_is_not_called_fine(self):
        # Counting an unjudgeable starter as agreement claims a check that
        # never happened.
        rows = lineup.flag_rows(LEAGUE, ROSTER, PLAYERS,
                                rk.merge({"page": {"QB": {"qb1": 1, "qb2": 2}}}))
        self.assertEqual(rows[1]["verdict"], "UNKNOWN")

    def test_flex_is_judged_on_the_overall_order(self):
        rows = self.rows(qb1=10, qb2=9, rb1=66, rb2=20)
        self.assertEqual(rows[1]["verdict"], "RED")
        self.assertEqual(rows[1]["slot"], "FLEX")


class RowShape(unittest.TestCase):

    def test_every_row_is_accepted_by_the_database(self):
        rows = lineup.flag_rows(LEAGUE, ROSTER, PLAYERS,
                                consensus(40, 2, 66, 20))
        conn = st.connect(":memory:")
        check_id = st.start_lineup_check(conn, "2026", 1, ["page"])
        for row in rows:
            st.add_lineup_flag(conn, check_id, **row)
        stored = st.lineup_flags(conn, check_id)
        self.assertEqual(len(stored), len(rows))
        self.assertEqual(stored[0]["league_name"], "OTG Alumni")
        self.assertEqual([r["slot"] for r in stored], ["QB", "FLEX"])

    def test_rows_survive_a_json_round_trip(self):
        import json
        rows = lineup.flag_rows(LEAGUE, ROSTER, PLAYERS,
                                consensus(40, 2, 66, 20))
        self.assertEqual(json.loads(json.dumps(rows)), rows)


class WeekContext(unittest.TestCase):
    """Opponents and points, read off records that may not carry either."""

    import nfl_week as nw

    def test_home_and_away_read_the_right_way_round(self):
        got = self.nw.matchups_from([
            {"team": "GB", "opponent": "CHI", "home": False},
            {"team": "DAL", "opponent": "PHI", "home": True}])
        self.assertEqual(got, {"GB": "at CHI", "DAL": "vs PHI"})

    def test_records_without_an_opponent_are_skipped(self):
        self.assertEqual(self.nw.matchups_from([{"team": "WAS"}]), {})

    def test_the_two_spellings_of_washington_are_reconciled(self):
        # Sleeper says WAS and ESPN says WSH; every player record in this
        # project comes from Sleeper, so Sleeper's spelling has to win or
        # the opponent never matches a roster.
        got = self.nw.matchups_from([{"team": "PHI", "opponent": "WSH",
                                      "home": True}])
        self.assertEqual(got, {"PHI": "vs WAS"})

    def test_points_prefer_half_ppr(self):
        got = self.nw.points_from([{"player_id": "1", "stats": {
            "pts_ppr": 20.0, "pts_half_ppr": 17.5, "pts_std": 15.0}}])
        self.assertEqual(got, {"1": 17.5})

    def test_a_record_with_no_points_at_all_is_skipped(self):
        self.assertEqual(self.nw.points_from([{"player_id": "1", "stats": {}}]),
                         {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
