#!/usr/bin/env python3
"""Tests for the Yahoo read-only client.

    python3 test_yahoo.py

No network and no credential: every fetch path runs with `_get` replaced, so
the awkward part - Yahoo's JSON, which is numbered collections of lists of
single-key fragments - is tested against real-shaped payloads rather than
hoped about.

The other thing under test is the agreement. Personal-use, read-only access
comes with terms that are easy to honour on the day and easy to lose later:
nothing may be stored, attribution travels with the data, and no function
may sweep every roster in a league. Those are assertions here so a later
change has to break a test to break the licence.
"""

import os
import sys
import unittest

import yahoo_client as yc


def collection(*members):
    """A Yahoo numbered collection."""
    out = {str(i): m for i, m in enumerate(members)}
    out["count"] = len(members)
    return out


LEAGUES = {"fantasy_content": {"users": collection({"user": [
    {"guid": "ABC"},
    {"games": collection({"game": [
        {"game_key": "461"},
        {"leagues": collection({"league": [{
            "league_key": "461.l.12345",
            "name": "Dynasty Warriors",
            "num_teams": "12",
            "scoring_type": "head",
            "current_week": "2",
            "url": "https://football.fantasysports.yahoo.com/f1/12345",
        }]})},
    ]})},
]})}}

TEAMS = {"fantasy_content": {"users": collection({"user": [
    {"guid": "ABC"},
    {"games": collection({"game": [
        {"game_key": "461"},
        {"teams": collection({"team": [[
            {"team_key": "461.l.12345.t.4"},
            {"name": "Prad's Team"},
        ]]})},
    ]})},
]})}}

ROSTER = {"fantasy_content": {"team": [
    {"team_key": "461.l.12345.t.4"},
    {"roster": {"0": {"players": collection(
        {"player": [[
            {"player_key": "461.p.31002"},
            {"name": {"full": "Justin Jefferson", "first": "Justin"}},
            {"editorial_team_abbr": "MIN"},
            {"primary_position": "WR"},
            {"status": "Q", "status_full": "Questionable"},
        ], {"selected_position": [{"position": "WR"}]}]},
        {"player": [[
            {"player_key": "461.p.40001"},
            {"name": {"full": "Bijan Robinson"}},
            {"editorial_team_abbr": "ATL"},
            {"primary_position": "RB"},
        ]]},
    )}, "count": 2}},
]}}

SCOREBOARD = {"fantasy_content": {"league": [
    {"league_key": "461.l.12345"},
    {"scoreboard": {"0": {"matchups": collection(
        {"matchup": {"0": {"teams": collection(
            {"team": [[{"team_key": "461.l.12345.t.4"}, {"name": "Mine"}],
                      {"team_points": {"total": "88.5"}},
                      {"team_projected_points": {"total": "92.1"}}]},
            {"team": [[{"team_key": "461.l.12345.t.9"}, {"name": "Theirs"}],
                      {"team_points": {"total": "71.2"}}]},
        )}}},
    )}}},
]}}

SETTINGS = {"fantasy_content": {"league": [
    {"league_key": "461.l.12345"},
    {"settings": [{
        "waiver_type": "FR",
        "uses_faab": "1",
        "faab_balance": "100",
        "trade_end_date": "2026-11-20",
        "playoff_start_week": "15",
    }]},
]}}

FREE_AGENTS = {"fantasy_content": {"league": [
    {"league_key": "461.l.12345"},
    {"players": collection({"player": [[
        {"player_key": "461.p.55555"},
        {"name": {"full": "Kaelon Black"}},
        {"editorial_team_abbr": "SF"},
        {"primary_position": "RB"},
    ]]})},
]}}


class Fetching(unittest.TestCase):
    """Every resource, with the network replaced."""

    def setUp(self):
        self.asked = []
        self.real = yc._get
        self.answer = {}
        yc._get = self.fake

    def tearDown(self):
        yc._get = self.real

    def fake(self, path):
        self.asked.append(path)
        return self.answer

    def test_leagues_come_back_readable(self):
        self.answer = LEAGUES
        got = yc.my_leagues()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["key"], "461.l.12345")
        self.assertEqual(got[0]["name"], "Dynasty Warriors")
        self.assertEqual(got[0]["teams"], 12)
        self.assertEqual(got[0]["week"], 2)

    def test_your_team_in_each_league(self):
        self.answer = TEAMS
        got = yc.my_teams()
        self.assertEqual(got[0]["key"], "461.l.12345.t.4")
        self.assertEqual(got[0]["league"], "461.l.12345")

    def test_a_roster_keeps_names_positions_and_injuries(self):
        self.answer = ROSTER
        got = yc.roster("461.l.12345.t.4", week=2)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0]["name"], "Justin Jefferson")
        self.assertEqual(got[0]["team"], "MIN")
        self.assertEqual(got[0]["position"], "WR")
        self.assertEqual(got[0]["status"], "Questionable")
        self.assertEqual(got[1]["name"], "Bijan Robinson")

    def test_the_week_reaches_the_request(self):
        self.answer = ROSTER
        yc.roster("461.l.12345.t.4", week=7)
        self.assertIn(";week=7", self.asked[0])

    def test_a_matchup_is_two_teams(self):
        self.answer = SCOREBOARD
        pairs = yc.matchup("461.l.12345", week=2)
        self.assertEqual(len(pairs), 1)
        mine, theirs = pairs[0]
        self.assertEqual(mine["name"], "Mine")
        self.assertEqual(theirs["name"], "Theirs")

    def test_free_agents_are_the_wire(self):
        self.answer = FREE_AGENTS
        got = yc.free_agents("461.l.12345", count=25)
        self.assertEqual(got[0]["name"], "Kaelon Black")
        self.assertIn("status=A", self.asked[0])
        self.assertIn("count=25", self.asked[0])

    def test_settings_say_how_claims_are_priced(self):
        self.answer = SETTINGS
        got = yc.settings("461.l.12345")
        self.assertTrue(got["uses_faab"])
        self.assertEqual(got["faab_budget"], 100)
        self.assertEqual(got["playoff_start"], 15)

    def test_an_empty_answer_is_empty_not_an_exception(self):
        self.answer = {}
        self.assertEqual(yc.my_leagues(), [])
        self.assertEqual(yc.roster("461.l.12345.t.4"), [])
        self.assertEqual(yc.matchup("461.l.12345"), [])


class RateLimits(unittest.TestCase):
    """Backing off is the developer's job under the agreement."""

    def setUp(self):
        self.real_get = yc._get
        self.real_sleep = yc.time.sleep
        self.slept = []
        yc.time.sleep = self.slept.append

    def tearDown(self):
        yc._get = self.real_get
        yc.time.sleep = self.real_sleep

    def throttled(self, times, then=None):
        import urllib.error
        state = {"n": 0}

        def fake(_path):
            state["n"] += 1
            if state["n"] <= times:
                raise urllib.error.HTTPError(
                    "u", 429, "Too Many Requests", {}, None)
            if then is None:
                raise AssertionError("asked once too often")
            return then
        yc._get = fake
        return state

    def test_a_throttled_request_waits_and_retries(self):
        self.throttled(2, then={"fantasy_content": {}})
        self.assertEqual(yc.get("/anything"), {"fantasy_content": {}})
        self.assertEqual(len(self.slept), 2)

    def test_the_waits_get_longer(self):
        self.throttled(2, then={})
        yc.get("/anything")
        self.assertGreater(self.slept[1], self.slept[0])

    def test_it_gives_up_rather_than_hammering(self):
        self.throttled(99)
        with self.assertRaises(yc.YahooError):
            yc.get("/anything")
        self.assertLessEqual(len(self.slept), yc.RETRIES)

    def test_a_refused_request_is_not_retried_forever(self):
        import urllib.error

        def forbidden(_path):
            raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
        yc._get = forbidden
        with self.assertRaises(yc.YahooError):
            yc.get("/anything")
        self.assertEqual(self.slept, [])


class TheAgreement(unittest.TestCase):
    """Terms that are easy to honour today and easy to lose later."""

    def test_the_attribution_is_the_wording_yahoo_asked_for(self):
        self.assertEqual(yc.ATTRIBUTION,
                         "Fantasy data provided by Yahoo Fantasy")
        self.assertIn("fantasysports.yahoo.com", yc.ATTRIBUTION_URL)

    def test_nothing_here_writes_to_the_database(self):
        # "Developer shall not store, cache or index the Yahoo Fantasy
        # Information." The simplest guard is that this module cannot: it
        # does not import the thing that would let it.
        source = open("yahoo_client.py").read()
        self.assertNotIn("import store", source)
        self.assertNotIn("import db", source)

    def test_there_is_no_way_to_sweep_every_roster(self):
        # The agreement bars compiling complete statistics for all players
        # in a fantasy league. A helper that fetched every team's roster
        # would be exactly that, so there is not one.
        names = [n for n in dir(yc) if not n.startswith("_")]
        for banned in ("all_rosters", "every_roster", "league_players"):
            self.assertNotIn(banned, names)

    def test_it_reads_and_never_writes(self):
        source = open("yahoo_client.py").read()
        for verb in ('method="POST"', 'method="PUT"', 'method="DELETE"'):
            # The one POST is the token refresh, which is OAuth, not fantasy
            # data - so it is allowed, and it is the only one.
            self.assertLessEqual(source.count(verb),
                                 1 if verb == 'method="POST"' else 0)


class WithoutCredentials(unittest.TestCase):
    """What happens on a machine that has not been set up yet."""

    def setUp(self):
        self.saved = {k: os.environ.pop(k, None) for k in
                      ("YAHOO_CLIENT_ID", "YAHOO_CLIENT_SECRET",
                       "YAHOO_REFRESH_TOKEN")}

    def tearDown(self):
        for key, value in self.saved.items():
            if value is not None:
                os.environ[key] = value

    def test_it_says_so_rather_than_failing_obscurely(self):
        self.assertFalse(yc.configured())

    def test_asking_for_a_token_explains_what_is_missing(self):
        yc._access["token"] = None
        with self.assertRaises(yc.YahooError) as caught:
            yc.token()
        self.assertIn("YAHOO_CLIENT_ID", str(caught.exception))

    def test_the_cli_exits_with_instructions_not_a_traceback(self):
        import contextlib
        import io
        out = io.StringIO()
        argv = sys.argv
        sys.argv = ["yahoo_client.py"]
        try:
            with contextlib.redirect_stdout(out):
                code = yc.main()
        finally:
            sys.argv = argv
        self.assertEqual(code, 1)
        self.assertIn("yahoo_auth_check.py --auth-url", out.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
