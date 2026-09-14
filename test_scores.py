#!/usr/bin/env python3
"""Tests for the cross-league scoreboard.

    python3 test_scores.py

Reading four apps to find four scores is the thing this replaces, so the
part worth testing is that it pairs the right two teams and counts what is
still to come.
"""

import unittest

import scores
import webapp

PLAYERS = {
    "1": {"position": "RB", "team": "GB", "full_name": "Played Already"},
    "2": {"position": "WR", "team": "DAL", "full_name": "Playing Later"},
    "3": {"position": "QB", "team": "KC", "full_name": "Their Guy"},
}
NAMES = {1: "My Team", 2: "Their Team"}
WEEK = {"kickoffs": {"GB": 1_000_000_000, "DAL": 9e18, "KC": 1_000_000_000},
        "games": {"GB": "at CHI", "DAL": "vs PHI", "KC": "at DEN"},
        "points": {"1": 11.0, "2": 9.5, "3": 18.0}}

SLOTS = ["RB", "WR"]
MINE = {"roster_id": 1, "matchup_id": 7, "points": 88.4,
        "starters": ["1", "2"], "starters_points": [12.5, 0]}
THEIRS = {"roster_id": 2, "matchup_id": 7, "points": 76.2,
          "starters": ["3"], "starters_points": [21.0]}
ELSEWHERE = {"roster_id": 3, "matchup_id": 9, "points": 120.0,
             "starters": ["3"], "starters_points": [30.0]}


class OneSide(unittest.TestCase):

    def test_a_started_game_is_not_still_to_play(self):
        got = scores.side(MINE, NAMES, PLAYERS, WEEK, SLOTS)
        self.assertEqual(got["to_play"], ["WR"])
        self.assertEqual([p["to_play"] for p in got["lineup"]], [False, True])

    def test_points_and_opponents_come_through(self):
        got = scores.side(MINE, NAMES, PLAYERS, WEEK, SLOTS)
        self.assertEqual(got["points"], 88.4)
        self.assertEqual(got["lineup"][0]["points"], 12.5)
        self.assertEqual(got["lineup"][0]["matchup"], "at CHI")

    def test_without_kickoff_times_nothing_is_claimed_to_be_pending(self):
        # A missing schedule should understate what is left rather than
        # invent points that may never arrive.
        blank = dict(WEEK, kickoffs={})
        self.assertEqual(scores.side(MINE, NAMES, PLAYERS, blank, SLOTS)["to_play"], [])

    def test_an_empty_slot_keeps_its_place_in_the_pairing(self):
        # Dropping it would shift every later slot against the wrong one.
        entry = dict(MINE, starters=["1", "0"], starters_points=[12.5, 0])
        got = scores.side(entry, NAMES, PLAYERS, WEEK, SLOTS)
        self.assertEqual([p["name"] for p in got["lineup"]],
                         ["Played Already (GB RB)", "Empty"])

    def test_a_projected_total_uses_real_points_where_the_game_is_done(self):
        # Adding a projection on top of points already scored would count
        # the same game twice.
        got = scores.side(MINE, NAMES, PLAYERS, WEEK, SLOTS)
        self.assertEqual(got["projected"], 22.0)   # 12.5 scored + 9.5 to come


class Names(unittest.TestCase):

    def test_a_chosen_team_name_beats_a_username(self):
        users = [{"user_id": "u1", "display_name": "pradm7",
                  "metadata": {"team_name": "Regulation Hotties"}},
                 {"user_id": "u2", "display_name": "someone"}]
        rosters = [{"roster_id": 1, "owner_id": "u1"},
                   {"roster_id": 2, "owner_id": "u2"},
                   {"roster_id": 3, "owner_id": None}]
        got = scores.team_names(users, rosters)
        self.assertEqual(got[1], "Regulation Hotties")
        self.assertEqual(got[2], "someone")
        self.assertEqual(got[3], "Unclaimed")


class Pairing(unittest.TestCase):
    """The opponent is the other roster in the same matchup, not any roster."""

    def board(self, matchups):
        import sleeper_client as sc
        real = (sc.league_rosters, sc.league_users, sc.league_matchups)
        sc.league_rosters = lambda _id: [{"roster_id": 1, "owner_id": "me"},
                                         {"roster_id": 2, "owner_id": "them"}]
        sc.league_users = lambda _id: [
            {"user_id": "me", "display_name": "pradm7"},
            {"user_id": "them", "display_name": "rival"}]
        sc.league_matchups = lambda _id, _w: matchups
        try:
            return scores.league_board(
                {"league_id": "5", "name": "LEHG",
                 "roster_positions": ["RB", "WR", "BN"]},
                "me", PLAYERS, 2, WEEK)
        finally:
            (sc.league_rosters, sc.league_users, sc.league_matchups) = real

    def test_the_right_opponent_is_chosen(self):
        got = self.board([MINE, THEIRS, ELSEWHERE])
        self.assertEqual(got["them"]["points"], 76.2)
        self.assertEqual(got["margin"], 12.2)

    def test_lineups_are_paired_slot_by_slot(self):
        rows = self.board([MINE, THEIRS])["rows"]
        self.assertEqual([r["slot"] for r in rows], ["RB", "WR"])
        self.assertEqual(rows[0]["theirs"]["name"], "Their Guy (KC QB)")
        self.assertIsNone(rows[1]["theirs"])

    def test_the_projected_margin_can_disagree_with_the_live_one(self):
        # Twelve points ahead with a receiver still to play is one point
        # ahead once everyone has played, which is the number that matters.
        got = self.board([MINE, THEIRS])
        self.assertEqual(got["margin"], 12.2)
        self.assertEqual(got["projected_margin"], 1.0)

    def test_a_bye_leaves_no_opponent_rather_than_failing(self):
        got = self.board([MINE])
        self.assertIsNone(got["them"])
        self.assertIsNone(got["margin"])


class Rendering(unittest.TestCase):

    def html(self, board):
        real = scores.board
        scores.board = lambda _u: board
        try:
            return webapp.render_scores("pradm7").decode()
        finally:
            scores.board = real

    BOARD = {"season": "2026", "week": 2, "leagues": [{
        "league_id": "5", "league_name": "LEHG",
        "us": {"roster_id": 1, "name": "My Team", "points": 88.4,
               "to_play": ["WR"], "projected": 97.9, "lineup": []},
        "them": {"roster_id": 2, "name": "Their Team", "points": 76.2,
                 "to_play": [], "projected": 76.2, "lineup": []},
        "rows": [{"slot": "WR",
                  "mine": {"player_id": "2", "name": "Playing Later (DAL WR)",
                           "pos": "WR", "points": 0.0, "projection": 9.5,
                           "matchup": "vs PHI", "to_play": True},
                  "theirs": {"player_id": "3", "name": "Their Guy (KC QB)",
                             "pos": "QB", "points": 18.0, "projection": 18.0,
                             "matchup": "at DEN", "to_play": False}}],
        "margin": 12.2, "projected_margin": 21.7}]}

    def test_the_margin_is_stated_in_words(self):
        self.assertIn("ahead by 12.2", self.html(self.BOARD))

    def test_the_leader_is_the_one_emphasised(self):
        html = self.html(self.BOARD)
        self.assertIn("class='score up'", html)
        self.assertEqual(html.count("class='score up'"), 1)

    def test_what_is_still_to_come_is_shown(self):
        html = self.html(self.BOARD)
        self.assertIn("1 to play (WR)", html)
        self.assertIn("97.9 projected", html)

    def test_where_it_is_heading_is_stated_too(self):
        self.assertIn("projected to win by 21.7", self.html(self.BOARD))

    def test_both_lineups_appear_side_by_side(self):
        html = self.html(self.BOARD)
        self.assertIn("Playing Later", html)
        self.assertIn("Their Guy", html)
        self.assertIn("class='half them", html)

    def test_a_failure_to_reach_sleeper_says_so(self):
        def boom(_u):
            raise RuntimeError("sleeper is down")
        real = scores.board
        scores.board = boom
        try:
            self.assertIn("sleeper is down",
                          webapp.render_scores("pradm7").decode())
        finally:
            scores.board = real


if __name__ == "__main__":
    unittest.main(verbosity=2)
