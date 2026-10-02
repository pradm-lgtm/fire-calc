#!/usr/bin/env python3
"""Yahoo scores and trade ideas, and the limits the agreement puts on them.

Section 2.c.x bars compiling complete statistics for all the players in a
fantasy league. That is not a footnote here, it is the shape of the file:
scores read the two teams in your matchup, and trade ideas are offered
against that same opponent rather than hunted across every roster.
"""

import unittest

import yahoo_live as yl


class FindingYourOwnMatchup(unittest.TestCase):
    """The scoreboard gives every pairing; only one of them is yours."""

    MINE = "470.l.715420.t.2"
    BOARD = [
        [{"key": "470.l.715420.t.4", "name": "Someone"},
         {"key": "470.l.715420.t.5", "name": "Else"}],
        [{"key": "470.l.715420.t.2", "name": "Slim Pickens"},
         {"key": "470.l.715420.t.7", "name": "Their Team"}],
    ]

    def setUp(self):
        import yahoo_client as yc
        self.yc, self.real = yc, yc.matchup
        yc.matchup = lambda key, week=None: self.BOARD

    def tearDown(self):
        self.yc.matchup = self.real

    def test_it_finds_the_pairing_you_are_in(self):
        us, them = yl.two_teams("470.l.715420", self.MINE)
        self.assertEqual(us["name"], "Slim Pickens")
        self.assertEqual(them["name"], "Their Team")

    def test_the_other_pairings_are_not_read(self):
        us, them = yl.two_teams("470.l.715420", self.MINE)
        for team in (us, them):
            self.assertNotIn(team["name"], ("Someone", "Else"))

    def test_a_week_you_are_not_in_gives_nothing(self):
        us, them = yl.two_teams("470.l.715420", "470.l.715420.t.99")
        self.assertIsNone(us)
        self.assertIsNone(them)

    def test_a_bye_week_has_no_opponent(self):
        self.BOARD = [[{"key": self.MINE, "name": "Slim Pickens"}]]
        us, them = yl.two_teams("470.l.715420", self.MINE)
        self.assertIsNotNone(us)
        self.assertIsNone(them)


class ALineupIsWhatIsStarting(unittest.TestCase):

    SQUAD = [
        {"key": "a", "name": "My QB", "position": "QB", "slot": "QB",
         "points": "22.3"},
        {"key": "b", "name": "My Flex", "position": "RB", "slot": "W/R/T",
         "points": "11.0"},
        {"key": "c", "name": "Benched", "position": "WR", "slot": "BN",
         "points": "0"},
        {"key": "d", "name": "Hurt", "position": "RB", "slot": "IR+",
         "points": "0"},
        {"key": "e", "name": "Long Term", "position": "WR", "slot": "IL",
         "points": "0"},
        {"key": "f", "name": "Not Rostered", "position": "WR", "slot": "NA",
         "points": "0"},
    ]

    def setUp(self):
        import yahoo_client as yc
        self.yc, self.real = yc, yc.roster
        yc.roster = lambda key, week=None: self.SQUAD

    def tearDown(self):
        self.yc.roster = self.real

    def names(self):
        return [p["name"] for p in yl.lineup_of("t.2", 5, {}, {})]

    def test_the_starters_are_there(self):
        self.assertIn("My QB", self.names())

    def test_the_bench_is_not(self):
        self.assertNotIn("Benched", self.names())

    def test_nor_any_kind_of_reserve(self):
        for who in ("Hurt", "Long Term", "Not Rostered"):
            self.assertNotIn(who, self.names())

    def test_the_flex_is_named_the_way_the_rest_of_the_code_names_it(self):
        flex = [p for p in yl.lineup_of("t.2", 5, {}, {})
                if p["name"] == "My Flex"][0]
        self.assertEqual(flex["slot"], "FLEX")

    def test_points_come_through_as_numbers(self):
        qb = [p for p in yl.lineup_of("t.2", 5, {}, {})
              if p["name"] == "My QB"][0]
        self.assertEqual(qb["points"], 22.3)

    def test_a_missing_score_is_nought_not_a_crash(self):
        self.SQUAD = [{"key": "a", "name": "x", "slot": "QB",
                       "points": None}]
        self.assertEqual(yl.lineup_of("t.2", 5, {}, {})[0]["points"], 0.0)


class TheScoreboard(unittest.TestCase):

    def setUp(self):
        import sleeper_client as sc
        import yahoo_client as yc
        self.yc, self.sc = yc, sc
        self.real = (yc.my_leagues, yc.my_teams, yc.matchup, yc.roster,
                     sc.all_players)
        yc.my_leagues = lambda: [{"key": "470.l.715420",
                                  "name": "The Minor League", "week": 5}]
        yc.my_teams = lambda: [{"key": "470.l.715420.t.2",
                                "name": "Slim Pickens",
                                "league": "470.l.715420"}]
        yc.matchup = lambda key, week=None: [[
            {"key": "470.l.715420.t.2", "name": "Slim Pickens",
             "points": "88.4", "projected": "102.1"},
            {"key": "470.l.715420.t.7", "name": "Their Team",
             "points": "91.0", "projected": "99.5"}]]
        yc.roster = lambda key, week=None: [
            {"key": "a", "name": "My QB", "position": "QB", "slot": "QB",
             "points": "22.3"}]
        sc.all_players = lambda refresh=False: {}

    def tearDown(self):
        (self.yc.my_leagues, self.yc.my_teams, self.yc.matchup,
         self.yc.roster, self.sc.all_players) = self.real

    def board(self):
        return yl.boards()[0]

    def test_both_sides_are_there(self):
        got = self.board()
        self.assertEqual(got["us"]["points"], 88.4)
        self.assertEqual(got["them"]["points"], 91.0)

    def test_the_margin_is_signed_from_your_side(self):
        self.assertEqual(self.board()["margin"], -2.6)

    def test_the_projected_margin_too(self):
        self.assertEqual(self.board()["projected_margin"], 2.6)

    def test_it_names_the_league(self):
        self.assertEqual(self.board()["league_name"], "The Minor League")

    def test_the_rows_pair_the_slots(self):
        """The choice you made in a slot only means anything next to the
        one they made in the same slot."""
        rows = self.board()["rows"]
        self.assertEqual(rows[0]["slot"], "QB")
        self.assertIsNotNone(rows[0]["mine"])
        self.assertIsNotNone(rows[0]["theirs"])


class WhatTheAgreementForbids(unittest.TestCase):
    """The narrowness is the point, not a gap to close later."""

    def test_trade_ideas_are_against_the_opponent_only(self):
        source = open("yahoo_live.py").read()
        self.assertIn("two_teams(key, mine[\"key\"], at)", source)
        # The Sleeper version loops every roster looking for a partner.
        self.assertNotIn("for other in rosters", source)

    def test_nothing_sweeps_every_roster_in_a_league(self):
        source = open("yahoo_live.py").read()
        self.assertNotIn("league_rosters", source)
        self.assertNotIn("league_users", source)

    def test_who_is_free_comes_from_the_endpoint_that_answers_that(self):
        """Rather than inferred by subtracting every roster from the
        player list, which is the compiling the agreement bars."""
        source = open("yahoo_live.py").read()
        self.assertIn("yc.free_agents(key", source)

    def test_the_limit_is_written_down_where_it_will_be_read(self):
        self.assertIn("2.c.x", yl.__doc__)
        self.assertIn("opponent", yl.__doc__.lower())

    def test_the_attribution_yahoo_asked_for_is_printed(self):
        source = open("yahoo_live.py").read()
        self.assertIn("yc.ATTRIBUTION", source)



class TheyReachThePages(unittest.TestCase):
    """A module nothing calls is a module that does not exist."""

    def test_the_scores_board_asks_for_them(self):
        source = open("scores.py").read()
        self.assertIn("out.extend(yahoo_boards(week_no))", source)

    def test_the_trade_board_asks_for_them(self):
        source = open("trades.py").read()
        self.assertIn("out.extend(yahoo_leagues(week, protect))", source)

    def test_neither_takes_the_sleeper_leagues_down_with_it(self):
        for path in ("scores.py", "trades.py"):
            with self.subTest(path=path):
                source = open(path).read()
                spot = source.index("def yahoo_")
                self.assertIn("except Exception", source[spot:spot + 900])

    def test_a_machine_with_no_yahoo_is_quiet_about_it(self):
        for path in ("scores.py", "trades.py"):
            with self.subTest(path=path):
                source = open(path).read()
                spot = source.index("def yahoo_")
                self.assertIn("yc.configured()", source[spot:spot + 900])

    def test_the_page_says_the_ideas_are_against_one_team(self):
        """Under the same heading as the others they would claim a search
        the agreement does not allow."""
        source = open("webapp.py").read()
        self.assertIn("Against ", source)
        self.assertIn("do not allow reading every roster", source)

if __name__ == "__main__":
    unittest.main(verbosity=2)
