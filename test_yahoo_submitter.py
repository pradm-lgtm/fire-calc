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


class TheProbeNeverPlacesAClaim(unittest.TestCase):
    """Opening the form is reversible. Placing a claim is not.

    A tool whose whole job is to find out what the page does has no
    business changing anything while it does so, and "it only clicked Add"
    is the sort of thing that is true right up until the markup moves.
    """

    def test_no_committing_word_is_ever_clicked(self):
        source = open("yahoo_submitter.py").read()
        for line in source.splitlines():
            if ".click(" not in line or line.strip().startswith("#"):
                continue
            for word in ys.COMMITTING:
                self.assertNotIn(word, line.lower(), line)

    def test_the_only_click_is_the_add_control(self):
        source = open("yahoo_submitter.py").read()
        clicks = [l.strip() for l in source.splitlines()
                  if ".click(" in l and not l.strip().startswith("#")]
        self.assertEqual(len(clicks), 1, clicks)
        self.assertTrue(clicks[0].startswith("control.click("), clicks[0])

    def test_it_says_so_before_it_clicks(self):
        """So a person reading the output knows what was done on their
        account, which is the whole basis for trusting the next step."""
        source = open("yahoo_submitter.py").read()
        self.assertIn("it does not", source)
        self.assertIn("No claim was placed", source)


class ReadingTheClaimForm(unittest.TestCase):
    """The question the Sleeper version answered only after two rewrites."""

    def test_a_drop_control_means_yahoo_wants_it_here(self):
        said = ys.describe_claim({"drop_controls": 2, "shape": "a new page"})
        self.assertIn("wants the drop chosen here", said)

    def test_none_means_a_claim_can_stand_alone(self):
        said = ys.describe_claim({"drop_controls": 0})
        self.assertIn("on its own", said)

    def test_a_bid_field_is_flagged_as_surprising(self):
        """These leagues are waiver priority. A FAAB box would mean the
        settings were read wrong, which is worth being told loudly."""
        said = ys.describe_claim({"drop_controls": 0, "bid_inputs": 1})
        self.assertIn("unexpected", said)

    def test_no_bid_field_is_not_flagged(self):
        said = ys.describe_claim({"drop_controls": 0, "bid_inputs": 0})
        self.assertNotIn("unexpected", said)

    def test_the_buttons_are_named_so_the_next_step_can_find_them(self):
        said = ys.describe_claim({"buttons": ["Submit", "Cancel"]})
        self.assertIn("Submit", said)
        self.assertIn("Cancel", said)

    def test_no_buttons_says_so_rather_than_printing_an_empty_list(self):
        self.assertIn("none found", ys.describe_claim({"buttons": []}))


class ItWillNotLaunchABrowserOfItsOwn(unittest.TestCase):
    """The throwaway profile is signed into nothing, which is the point.

    browser_context() launches one when nothing is listening. A probe that
    does that reports "not signed in to Yahoo" about a browser the person
    has never seen, while their real Chrome sits there logged in - and no
    amount of signing in again will change what it says. The Sleeper side
    was fixed for this; reusing the helper brought it back.
    """

    def setUp(self):
        self.real_probe = ys.submitter.cdp_probe
        self.real_attach = ys.submitter.attach
        self.real_ctx = ys.submitter.browser_context

    def tearDown(self):
        ys.submitter.cdp_probe = self.real_probe
        ys.submitter.attach = self.real_attach
        ys.submitter.browser_context = self.real_ctx

    def test_with_nothing_listening_it_says_so_and_stops(self):
        ys.submitter.cdp_probe = lambda *a, **k: (False, "ConnectionRefused")

        def explode(*a, **k):
            raise AssertionError("launched a browser of its own")
        ys.submitter.browser_context = explode
        ys.submitter.attach = explode
        self.assertEqual(ys.do_probe(None, "470.l.715420"),
                         ys.submitter.NEEDS_LOGIN)

    def test_it_never_calls_browser_context_at_all(self):
        """Not even when Chrome IS listening - attach is the only path."""
        ys.submitter.cdp_probe = lambda *a, **k: (True, "Chrome/152")

        def explode(*a, **k):
            raise AssertionError("used browser_context instead of attach")
        ys.submitter.browser_context = explode
        ys.submitter.attach = lambda pw: (_ for _ in ()).throw(
            RuntimeError("attach reached, which is correct"))
        with self.assertRaises(RuntimeError):
            ys.do_probe(None, "470.l.715420")


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
