#!/usr/bin/env python3
"""Yahoo scores and trade ideas, and the limits the agreement puts on them.

Scores read the two teams in your matchup, because a scoreboard is about
a matchup. Trade ideas look at every team, as the Sleeper half does -
yahoo_live's docstring sets out the reading of section 2.c.x that an
earlier, stricter version here got wrong.

The contract tests at the bottom are the ones that matter most. A Yahoo
league travels through the same stored run and the same page as a
Sleeper one, so a key it does not carry is not a Yahoo bug, it is a
trade run that fails for every league at once.
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


class TheScoreboardTravelsToThePage(unittest.TestCase):
    """The same contract the trade ideas broke, checked for the scores.

    A Yahoo board joins the Sleeper ones in one list and is then sorted,
    totalled and drawn by code written for a Sleeper board. These run
    those readers over a Yahoo one rather than comparing key lists by
    eye, which is how the missing trade key got through.
    """

    PAIRING = [[
        {"key": "t.2", "name": "Slim Pickens", "points": "140.9",
         "projected": "140.9"},
        {"key": "t.7", "name": "Hey Yo Trey Trey!", "points": "118.8",
         "projected": "118.8"}]]

    def board(self, pairing=None):
        import sleeper_client as sc
        import yahoo_client as yc
        real = (yc.my_leagues, yc.my_teams, yc.matchup, yc.roster,
                sc.all_players)
        board = pairing if pairing is not None else self.PAIRING
        yc.my_leagues = lambda: [{"key": "470.l.1", "name": "The Minor League",
                                  "week": 5}]
        yc.my_teams = lambda: [{"key": "t.2", "name": "Slim Pickens",
                                "league": "470.l.1"}]
        yc.matchup = lambda key, week=None: board
        yc.roster = lambda key, week=None: [
            {"key": "a", "name": "My QB", "position": "QB", "slot": "QB",
             "points": "22.3"}]
        sc.all_players = lambda refresh=False: {}
        try:
            return yl.boards(5)[0]
        finally:
            (yc.my_leagues, yc.my_teams, yc.matchup, yc.roster,
             sc.all_players) = real

    def test_the_scoreboard_sort_can_order_it(self):
        import scores
        self.assertIsNotNone(scores.sort_key(self.board()))

    def test_what_is_left_to_play_can_be_counted(self):
        import scores
        self.assertEqual(scores.left_to_play(self.board()), 0)

    def test_the_page_can_draw_it(self):
        import webapp
        self.assertIn("QB", webapp.matchup_rows(self.board()))

    def test_a_bye_week_board_survives_all_three(self):
        """One side missing is the shape most likely to break a reader."""
        import scores
        import webapp
        got = self.board(pairing=[[
            {"key": "t.2", "name": "Slim Pickens", "points": "140.9"}]])
        self.assertIsNone(got["them"])
        self.assertIsNotNone(scores.sort_key(got))
        self.assertEqual(scores.left_to_play(got), 0)
        webapp.matchup_rows(got)


class WhatTheAgreementActuallySays(unittest.TestCase):
    """Trades look at every team, the way the Sleeper half does.

    An earlier version here offered them against the week's opponent
    alone, reading "shall not compile complete statistics for all the
    players in a fantasy league" as covering a tool that looks at the
    league you play in. That clause is about building a statistics
    product out of Yahoo's data; reading your own league is the Personal
    Use the agreement grants, and Yahoo serves a league teams endpoint
    for it. The caution made the Yahoo half quietly worse than the
    Sleeper half for no reason the agreement gives.
    """

    def test_every_team_is_considered(self):
        source = open("yahoo_live.py").read()
        self.assertIn("for other in yc.league_teams(key):", source)

    def test_your_own_team_is_not_offered_a_trade_with_itself(self):
        source = open("yahoo_live.py").read()
        spot = source.index("for other in yc.league_teams(key):")
        self.assertIn('if other["key"] == mine["key"]',
                      source[spot:spot + 200])

    def test_the_offers_name_who_they_are_with(self):
        source = open("yahoo_live.py").read()
        self.assertIn('offer["with"] = other.get("name")', source)

    def test_the_best_ones_come_first_across_the_whole_league(self):
        """Sorted after gathering, not within each team."""
        source = open("yahoo_live.py").read()
        spot = source.index("found.sort(")
        self.assertIn("my_gain", source[spot:spot + 150])

    def test_scores_still_read_only_the_two_teams_in_your_matchup(self):
        """Not for the agreement's sake - a scoreboard is a matchup."""
        source = open("yahoo_live.py").read()
        self.assertIn("two_teams(key, mine[\"key\"], at)", source)

    def test_who_is_free_comes_from_the_endpoint_that_answers_that(self):
        source = open("yahoo_live.py").read()
        self.assertIn("yc.free_agents(key", source)

    def test_the_attribution_yahoo_asked_for_is_printed(self):
        source = open("yahoo_live.py").read()
        self.assertIn("yc.ATTRIBUTION", source)



class PrintingWhatItFound(unittest.TestCase):
    """The run worked and the printing crashed.

    Seven ideas in one league and eight in the other, then a KeyError on
    'opponent' - a key removed when trades stopped being opponent-only
    and left behind in the code that prints them. A summary line that
    reads a field the data no longer has is a crash that claims the work
    failed when it did not.
    """

    def said(self, boards):
        import contextlib
        import io
        import sleeper_client as sc
        import sys
        import yahoo_client as yc
        real = (yl.trade_ideas, sc.all_players, yc.configured, sys.argv)
        sc.all_players = lambda refresh=False: {
            "a": {"full_name": "My Man", "position": "RB", "team": "GB"},
            "b": {"full_name": "Their Man", "position": "WR",
                  "team": "BAL"}}
        yl.trade_ideas = lambda w=None, p=(), verbose=False: boards
        yc.configured = lambda: True
        sys.argv = ["yahoo_live.py", "--trades"]
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                yl.main()
        finally:
            (yl.trade_ideas, sc.all_players, yc.configured,
             sys.argv) = real
        return out.getvalue()

    OFFER = {"give": ["a"], "get": ["b"], "my_gain": 453,
             "their_gain": 1601, "my_pct": 5, "their_pct": 18,
             "tilt": 3, "spots": 0, "changes": [],
             "their_record": (4, 1, 0), "with": "Team Ruhi"}
    LEAGUE = {"league_name": "L", "summary": "A paragraph.",
              "offers": [OFFER]}

    def board(self, **changed):
        got = dict(self.LEAGUE)
        got.update(changed)
        return [got]

    def test_it_names_who_the_trade_is_with(self):
        said = self.said(self.board())
        self.assertIn("Team Ruhi", said)

    def test_it_names_both_sides_of_the_deal(self):
        said = self.said(self.board())
        self.assertIn("My Man", said)
        self.assertIn("Their Man", said)

    def test_it_says_the_gain_as_a_share_of_the_lineup(self):
        """Raw value units made every idea look like a robbery.

        "you +453, them +1601" is two lineups with different baselines,
        on a scale where the best player in the game is ten thousand.
        The page has always shown the percentage; the terminal showed
        the raw number and read as a tool handing games away.
        """
        said = self.said(self.board())
        self.assertIn("+5%", said)
        self.assertIn("+18%", said)
        self.assertNotIn("+453", said)
        self.assertNotIn("+1601", said)

    def test_it_says_how_the_other_team_is_doing(self):
        said = self.said(self.board())
        self.assertIn("4-1", said)

    def test_a_league_with_nothing_says_so(self):
        said = self.said(self.board(offers=[]))
        self.assertIn("nothing worth proposing", said)

    def test_an_offer_with_no_partner_named_does_not_crash(self):
        bare = {k: v for k, v in self.OFFER.items() if k != "with"}
        said = self.said(self.board(offers=[bare]))
        self.assertIn("My Man", said)

    def test_an_offer_with_no_record_does_not_crash(self):
        bare = {k: v for k, v in self.OFFER.items() if k != "their_record"}
        said = self.said(self.board(offers=[bare]))
        self.assertIn("My Man", said)

    def test_it_reads_no_field_the_offers_do_not_carry(self):
        """"opponent" went when trades stopped being opponent-only."""
        source = open("yahoo_live.py").read()
        self.assertNotIn("got['opponent']", source)
        self.assertNotIn('got["opponent"]', source)


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

    def test_the_page_does_not_claim_a_narrower_search_than_it_made(self):
        source = open("webapp.py").read()
        self.assertNotIn("do not allow reading every roster", source)


class TheSameShapeAsTheSleeperHalf(unittest.TestCase):
    """A Yahoo league has to survive every reader a Sleeper one does.

    trades.board puts both kinds in one list, and written_out, the stored
    run and the page then read them through the same code. `their_record`
    was missing from the Yahoo offers, so written_out raised KeyError on
    the first one and the whole trade run failed - losing the Sleeper
    leagues, which were fine.

    So these drive the real trade_ideas against stub endpoints and feed
    what comes out to the real readers. Nothing here lists the keys by
    hand: a list written next to the code it checks agrees with it by
    construction and proves nothing.
    """

    PLAYERS = {
        "rb1": {"full_name": "My First Back", "position": "RB", "team": "GB"},
        "rb2": {"full_name": "My Spare Back", "position": "RB", "team": "KC"},
        "wr1": {"full_name": "My Weak End", "position": "WR", "team": "NO"},
        "wra": {"full_name": "Their Good End", "position": "WR", "team": "DET"},
        "wrb": {"full_name": "Their Other End", "position": "WR", "team": "SF"},
        "rbx": {"full_name": "Their Weak Back", "position": "RB", "team": "LV"},
    }
    VALUES = {
        "rb1": {"value": 5000.0, "position": "RB"},
        "rb2": {"value": 4800.0, "position": "RB"},
        "wr1": {"value": 1000.0, "position": "WR"},
        "wra": {"value": 4900.0, "position": "WR"},
        "wrb": {"value": 4700.0, "position": "WR"},
        "rbx": {"value": 1100.0, "position": "RB"},
    }
    SQUADS = {"t.1": ["rb1", "rb2", "wr1"], "t.2": ["wra", "wrb", "rbx"]}

    def ideas(self, records=None):
        """trade_ideas over one stubbed league, with the real trade code."""
        import sleeper_client as sc
        import trade_values as tv
        import yahoo_bridge as yb
        import yahoo_client as yc
        import yahoo_waivers as yw
        real = (sc.all_players, sc.current_state, tv.settings_from, tv.fetch,
                yb.bridge, yc.my_leagues, yc.my_teams, yc.roster_slots,
                yc.roster, yc.free_agents, yc.league_teams,
                yw.as_league, yl.byes_for, yl.records_in)

        def squad_of(team_key, week=None):
            return [{"key": f"{team_key}:{pid}", "name": pid, "slot": "BN"}
                    for pid in self.SQUADS[team_key]]

        sc.all_players = lambda refresh=False: dict(self.PLAYERS)
        sc.current_state = lambda: {"season": 2026, "week": 5}
        tv.settings_from = lambda league: {"teams": 10, "ppr": 0.5,
                                           "quarterbacks": 1}
        tv.fetch = lambda *a, **k: (dict(self.VALUES), "a stub")
        yb.bridge = lambda squad, players: (
            {w["key"]: w["key"].split(":")[1] for w in squad}, [])
        yc.my_leagues = lambda: [{"key": "470.l.1", "name": "The Minor League",
                                  "week": 5, "teams": 10}]
        yc.my_teams = lambda: [{"key": "t.1", "name": "Mine",
                                "league": "470.l.1"}]
        yc.roster_slots = lambda key: ["RB", "WR", "BN"]
        yc.roster = squad_of
        yc.free_agents = lambda key, count=50, position=None: []
        yc.league_teams = lambda key: [{"key": "t.1", "name": "Mine"},
                                       {"key": "t.2", "name": "Bell Biv Deebo"}]
        yw.as_league = lambda league, slots: {
            "league_id": league["key"], "name": league.get("name"),
            "roster_positions": slots, "total_rosters": 10,
            "scoring_settings": {"rec": 0.5}, "settings": {}}
        yl.byes_for = lambda season: {}
        table = ({"t.1": (3, 2, 0), "t.2": (4, 1, 0)}
                 if records is None else records)
        yl.records_in = lambda key: table
        try:
            return yl.trade_ideas(5)
        finally:
            (sc.all_players, sc.current_state, tv.settings_from, tv.fetch,
             yb.bridge, yc.my_leagues, yc.my_teams, yc.roster_slots,
             yc.roster, yc.free_agents, yc.league_teams,
             yw.as_league, yl.byes_for, yl.records_in) = real

    def test_the_stubs_actually_produce_an_offer(self):
        """Otherwise every test below passes over an empty list."""
        got = self.ideas()
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0]["offers"], "no offer, so nothing is proved")

    def test_a_league_carries_every_key_a_sleeper_league_does(self):
        mine = set(self.ideas()[0])
        sleeper = {"league_id", "league_name", "my_record", "offers",
                   "shape", "summary", "settings", "values"}
        self.assertEqual(sleeper - mine, set())

    def test_the_stored_run_can_be_written(self):
        """The failure the user saw, at the point it actually happened."""
        import trades
        got = {"season": 2026, "week": 5, "source": "a stub",
               "leagues": self.ideas()}
        written = trades.written_out(got, dict(self.PLAYERS))
        self.assertEqual(len(written), 1)
        self.assertTrue(written[0]["offers"])

    def test_the_page_can_draw_a_stored_offer(self):
        import trades
        import webapp
        got = {"season": 2026, "week": 5, "source": "a stub",
               "leagues": self.ideas()}
        written = trades.written_out(got, dict(self.PLAYERS))
        for offer in written[0]["offers"]:
            self.assertIn("Bell Biv Deebo", webapp.stored_trade_card(offer))

    def test_the_page_can_draw_a_live_offer(self):
        import webapp
        got = self.ideas()[0]
        for offer in got["offers"]:
            drawn = webapp.trade_card(offer, dict(self.PLAYERS))
            self.assertIn("Bell Biv Deebo", drawn)

    def test_the_record_reaches_the_offer_and_the_summary(self):
        got = self.ideas()[0]
        self.assertEqual(got["my_record"], (3, 2, 0))
        self.assertEqual(got["offers"][0]["their_record"], (4, 1, 0))
        self.assertIn("3-2", got["summary"])

    def test_a_league_with_no_standings_reads_as_unplayed(self):
        """Not as 0-0, which would be a claim about the season."""
        got = self.ideas(records={})
        self.assertIn("No games have counted yet", got[0]["summary"])
        self.assertTrue(got[0]["offers"], "the ideas survive a missing record")

    def test_standings_that_fail_cost_a_paragraph_and_not_the_ideas(self):
        import contextlib
        import io
        import yahoo_client as yc
        real = yc.standings

        def broken(key):
            raise RuntimeError("Yahoo said no")

        yc.standings = broken
        said = io.StringIO()
        try:
            with contextlib.redirect_stdout(said):
                self.assertEqual(yl.records_in("470.l.1"), {})
        finally:
            yc.standings = real
        self.assertIn("Yahoo said no", said.getvalue())


class TheDocstringsMatchTheCode(unittest.TestCase):
    """A comment that describes the version before last is a lie.

    The opponent-only search is gone. Every place that still described it
    was telling a reader the opposite of what the code does.
    """

    def test_no_file_still_claims_the_search_is_opponent_only(self):
        """The test file is left out: it quotes the old wording on purpose,
        to say what changed and why."""
        for path in ("yahoo_live.py", "trades.py", "webapp.py"):
            with self.subTest(path=path):
                source = open(path).read()
                self.assertNotIn("against the opponent only", source)
                self.assertNotIn("against that same opponent", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
