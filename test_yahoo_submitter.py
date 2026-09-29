#!/usr/bin/env python3
"""The browser half of Yahoo, which exists because the API is read-only.

Everything here that can be tested without a browser is tested without
one: the key-to-URL conversion, the signed-out judgement, and the report.
What cannot is deliberately thin, and is a probe that clicks nothing until
its output has been read by a person.
"""

import unittest

import yahoo_submitter as ys


class KeysIntoAddresses(unittest.TestCase):
    """The API speaks in league keys; every URL wants the middle number."""

    def test_a_league_key_gives_up_its_number(self):
        self.assertEqual(ys.league_number("470.l.715420"), "715420")

    def test_both_of_his_leagues(self):
        self.assertEqual(ys.league_number("470.l.1533743"), "1533743")

    def test_a_bare_number_is_taken_as_one(self):
        self.assertEqual(ys.league_number("715420"), "715420")

    def test_a_team_key_is_not_a_league_key(self):
        """It ends in .t.2, and quietly reading it as a league would open
        the wrong page - the failure mode that cost a round on Sleeper."""
        self.assertIsNone(ys.league_number("470.l.715420.t.2"))

    def test_nonsense_is_refused_rather_than_guessed(self):
        for bad in ("", None, "nonsense", "l.715420", "470.l."):
            with self.subTest(bad=bad):
                self.assertIsNone(ys.league_number(bad))
                self.assertIsNone(ys.players_url(bad))

    def test_the_players_url_is_the_free_agents_list(self):
        url = ys.players_url("470.l.715420")
        self.assertIn("/f1/715420/players", url)
        self.assertIn("status=A", url)

    def test_a_team_key_becomes_that_team_page(self):
        self.assertEqual(ys.team_url("470.l.715420.t.2"),
                         f"{ys.SITE}/f1/715420/2")

    def test_a_league_key_is_not_a_team(self):
        self.assertIsNone(ys.team_url("470.l.715420"))


class KnowingYouAreSignedOut(unittest.TestCase):

    def test_the_login_page_is_recognised(self):
        self.assertTrue(ys.signed_out(
            "https://login.yahoo.com/?done=x", "Sign in to Yahoo"))

    def test_a_redirect_to_login_counts(self):
        self.assertTrue(ys.signed_out(
            "https://football.fantasysports.yahoo.com/login", "Yahoo"))

    def test_a_real_page_does_not(self):
        self.assertFalse(ys.signed_out(
            "https://football.fantasysports.yahoo.com/f1/715420/players",
            "Players - The Minor League"))

    def test_a_signed_in_page_carrying_a_sign_in_link_does_not(self):
        """Judged from the address, because every page has such a link."""
        self.assertFalse(ys.signed_out(
            "https://football.fantasysports.yahoo.com/f1/715420",
            "Slim Pickens - The Minor League"))


class TheReport(unittest.TestCase):
    """What the probe found, said plainly enough to act on."""

    def test_it_says_when_it_found_nothing(self):
        said = ys.describe({"add_controls": 0, "rows": 0})
        self.assertIn("none found", said)

    def test_it_counts_what_it_saw(self):
        said = ys.describe({"add_controls": 25, "rows": 25, "dialog": False})
        self.assertIn("25", said)
        self.assertNotIn("none found", said)

    def test_a_drop_control_is_reported_as_yahoo_asking_for_one(self):
        said = ys.describe({"add_controls": 1, "drop_controls": 3})
        self.assertIn("asks for the drop", said)

    def test_no_drop_control_is_reported_as_maybe_not_needing_one(self):
        said = ys.describe({"add_controls": 1, "drop_controls": 0})
        self.assertIn("may not need one", said)

    def test_not_having_looked_is_not_the_same_as_having_found_none(self):
        """The distinction the Sleeper version got wrong for two rounds."""
        said = ys.describe({"add_controls": 1, "drop_controls": None})
        self.assertIn("not reached", said)
        self.assertNotIn("may not need one", said)


class TheProbeRefusesBeforeItOpensAnything(unittest.TestCase):

    def test_a_bad_key_never_reaches_a_browser(self):
        def explode(*a, **k):
            raise AssertionError("opened a browser for a key it should "
                                 "have refused")
        real = ys.submitter.browser_context
        ys.submitter.browser_context = explode
        try:
            self.assertEqual(ys.do_probe(None, "not-a-key"), 1)
        finally:
            ys.submitter.browser_context = real


if __name__ == "__main__":
    unittest.main(verbosity=2)
