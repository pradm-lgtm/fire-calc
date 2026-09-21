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


class Refusals(unittest.TestCase):
    """Yahoo's noes, told apart from each other."""

    def test_a_403_is_explained_as_provisioning_not_code(self):
        said = yc.complain(403, '{"error": {"description": "This application '
                                'is not authorized to use the Fantasy API"}}')
        self.assertIn("not authorised for the Fantasy API", said)
        self.assertIn("Confirmation Form", said)
        self.assertNotIn("HTTP 403", said)

    def test_a_401_points_at_re_authorising(self):
        self.assertIn("--auth-url", yc.complain(401, "token expired"))

    def test_anything_else_is_reported_as_itself(self):
        said = yc.complain(404, "no such league")
        self.assertIn("404", said)
        self.assertIn("no such league", said)

    def test_a_403_that_is_not_about_authorisation_is_not_reworded(self):
        said = yc.complain(403, "rate limit exceeded for this account")
        self.assertIn("403", said)

    def test_the_client_id_is_identified_without_being_printed(self):
        os.environ["YAHOO_CLIENT_ID"] = "abcdefghijklmnop"
        try:
            said = yc.complain(403, "This application is not authorized")
        finally:
            os.environ.pop("YAHOO_CLIENT_ID", None)
        self.assertIn("ijklmnop", said)
        self.assertNotIn("abcdefgh", said)


class Ready(unittest.TestCase):
    """The one-line check for whether the grant has landed."""

    def setUp(self):
        self.real = yc.get
        os.environ["YAHOO_CLIENT_ID"] = "xxxxxxxxXq9goioO"

    def tearDown(self):
        yc.get = self.real
        os.environ.pop("YAHOO_CLIENT_ID", None)

    def run_it(self, verbose=False):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = yc.ready(verbose=verbose)
        return code, out.getvalue()

    def refuse_all(self, why="Yahoo says this application is not authorised "
                             "for the Fantasy API."):
        def no(_p):
            raise yc.YahooError(why)
        yc.get = no

    def answer_only(self, working):
        """Yahoo answers `working` and refuses everything else."""
        def some(path):
            if path == working:
                return {"fantasy_content": {}}
            raise yc.YahooError(
                "Yahoo says this application is not authorised for the "
                "Fantasy API.")
        yc.get = some

    def test_a_working_app_says_yes(self):
        yc.get = lambda _p: {"fantasy_content": {}}
        code, said = self.run_it()
        self.assertEqual(code, 0)
        self.assertIn("Yes.", said)
        self.assertIn("Xq9goioO", said)

    def test_an_unprovisioned_app_says_wait_not_fix(self):
        self.refuse_all()
        code, said = self.run_it()
        self.assertEqual(code, 1)
        self.assertIn("Not yet.", said)
        self.assertIn("the application rather than", said)

    def test_a_different_failure_is_not_called_provisioning(self):
        self.refuse_all("could not reach Yahoo: timed out")
        code, said = self.run_it()
        self.assertEqual(code, 1)
        self.assertNotIn("Not yet.", said)
        self.assertIn("Something else is wrong", said)

    def test_it_asks_more_than_one_endpoint(self):
        """One endpoint's quirk and a missing grant look the same.

        The whole reason this check exists is to tell those apart, so
        a version that asks once is a version that cannot.
        """
        asked = []

        def note(path):
            asked.append(path)
            raise yc.YahooError("not authorised for the Fantasy API")
        yc.get = note
        self.run_it()
        self.assertEqual(asked, list(yc.PROBES))
        self.assertGreater(len(asked), 1)

    def test_one_endpoint_answering_is_enough_to_say_yes(self):
        """Access is access. If anything answers, stop telling him to wait."""
        for probe in yc.PROBES:
            with self.subTest(probe=probe):
                self.answer_only(probe)
                code, said = self.run_it()
                self.assertEqual(code, 0)
                self.assertIn("Yes.", said)
                self.assertIn(probe, said)

    def test_it_stops_asking_once_something_answers(self):
        asked = []

        def first_one_works(path):
            asked.append(path)
            return {"fantasy_content": {}}
        yc.get = first_one_works
        self.run_it()
        self.assertEqual(asked, [yc.PROBES[0]])

    def test_a_refusal_that_is_only_partly_about_scope_is_not_not_yet(self):
        """Half a refusal is a different animal, and saying "wait" hides it."""
        def mixed(path):
            if path == yc.PROBES[0]:
                raise yc.YahooError("not authorised for the Fantasy API")
            raise yc.YahooError("could not reach Yahoo: timed out")
        yc.get = mixed
        code, said = self.run_it()
        self.assertEqual(code, 1)
        self.assertNotIn("Not yet.", said)
        self.assertIn("timed out", said)

    def test_waiting_says_what_to_do_the_day_it_lands(self):
        """Yahoo fixes scope at consent, and his tokens predate the grant.

        So a token minted today can keep being refused after access is
        attached, and the answer is to consent again - which is only
        useful if it is written down where he will be reading.
        """
        self.refuse_all()
        _code, said = self.run_it()
        self.assertIn("yahoo_auth_check.py --auth-url", said)
        self.assertIn("scope", said)

    def test_diagnose_prints_yahoo_own_words(self):
        self.refuse_all("Yahoo says this application is not authorised for "
                        "the Fantasy API.\nbody: <html>go away</html>")
        _code, quiet = self.run_it()
        _code, loud = self.run_it(verbose=True)
        self.assertNotIn("go away", quiet)
        self.assertIn("go away", loud)
        for probe in yc.PROBES:
            self.assertIn(probe, loud)


class TellingYouOnce(unittest.TestCase):
    """The daily check that announces access and then stops.

    There is no credential to wait for and no email promised, so the only
    way to know is to ask - and a notification that repeats every morning
    afterwards is one you stop reading.
    """

    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()
        self.real_told = yc.TOLD
        self.real_get = yc.get
        self.real_configured = yc.configured
        self.real_announce = yc.announce
        yc.TOLD = os.path.join(self.dir, ".yahoo-ready")
        yc.configured = lambda: True
        self.said = []
        yc.announce = self.said.append

    def tearDown(self):
        yc.TOLD = self.real_told
        yc.get = self.real_get
        yc.configured = self.real_configured
        yc.announce = self.real_announce

    def refused(self):
        def no(_p):
            raise yc.YahooError("not authorised for the Fantasy API")
        yc.get = no

    def granted(self):
        yc.get = lambda _p: {"fantasy_content": {}}

    def test_it_says_nothing_while_access_is_refused(self):
        self.refused()
        self.assertEqual(yc.watch_for_access(), 1)
        self.assertEqual(self.said, [])

    def test_it_announces_the_day_access_lands(self):
        self.granted()
        self.assertEqual(yc.watch_for_access(), 0)
        self.assertEqual(len(self.said), 1)
        self.assertIn("live", self.said[0])

    def test_it_does_not_announce_twice(self):
        self.granted()
        yc.watch_for_access()
        yc.watch_for_access()
        yc.watch_for_access()
        self.assertEqual(len(self.said), 1)

    def test_it_announces_when_only_the_second_endpoint_answers(self):
        """The announcement follows the same rule as the check itself."""
        def second_only(path):
            if path == yc.PROBES[0]:
                raise yc.YahooError("not authorised for the Fantasy API")
            return {"fantasy_content": {}}
        yc.get = second_only
        self.assertEqual(yc.watch_for_access(), 0)
        self.assertEqual(len(self.said), 1)

    def test_a_machine_with_no_credentials_stays_quiet(self):
        yc.configured = lambda: False
        self.granted()
        self.assertEqual(yc.watch_for_access(), 1)
        self.assertEqual(self.said, [])

    def test_the_marker_is_not_something_to_commit(self):
        """Under either name the file goes by.

        In this repository it is gitignore.fantasy, because the project
        shares a checkout with other things. The migrate script copies it
        into the deployed repository as a plain .gitignore, so a test that
        knows only the first name fails there for a reason that has
        nothing to do with what it is checking.
        """
        here = os.path.dirname(os.path.abspath(__file__))
        for name in ("gitignore.fantasy", ".gitignore"):
            path = os.path.join(here, name)
            if os.path.exists(path):
                with open(path) as fh:
                    self.assertIn(".yahoo-ready", fh.read())
                return
        self.fail("no gitignore found next to the tests")


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
    """What happens on a machine that has not been set up yet.

    Emptying the environment is not enough: configured() loads .env before it
    looks, so on a machine that has one - which is any machine this actually
    runs on - the variables come straight back and the test passes for the
    wrong reason, or fails for one. The file has to be taken out of the
    picture too.
    """

    def setUp(self):
        self.saved = {k: os.environ.pop(k, None) for k in
                      ("YAHOO_CLIENT_ID", "YAHOO_CLIENT_SECRET",
                       "YAHOO_REFRESH_TOKEN")}
        self.real_load = yc.localenv.load
        yc.localenv.load = lambda *_a, **_k: []

    def tearDown(self):
        yc.localenv.load = self.real_load
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
