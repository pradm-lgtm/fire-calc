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

    def test_a_team_key_becomes_that_team_page(self):
        self.assertEqual(ys.team_url("470.l.715420.t.2"),
                         f"{ys.SITE}/f1/715420/2")

    def test_a_league_key_is_not_a_team(self):
        self.assertIsNone(ys.team_url("470.l.715420"))


class StraightToTheForm(unittest.TestCase):
    """The API says who is available; the browser only places the claim.

    Walking the fifty-row players page to find an Add link was scraping
    for something an endpoint already answers - and it was the page Yahoo
    refused with ERR_BLOCKED_BY_RESPONSE the second time it was asked.
    """

    def test_a_player_key_gives_up_its_id(self):
        self.assertEqual(ys.player_id("470.p.12345"), "12345")

    def test_a_league_key_is_not_a_player(self):
        self.assertIsNone(ys.player_id("470.l.715420"))

    def test_the_claim_address_carries_both_ids(self):
        url = ys.claim_url("470.l.715420", "470.p.12345")
        self.assertIn("/f1/715420/addplayer", url)
        self.assertIn("apid=12345", url)

    def test_a_bad_id_gives_no_address_rather_than_a_broken_one(self):
        self.assertIsNone(ys.claim_url("470.l.715420", "junk"))
        self.assertIsNone(ys.claim_url("junk", "470.p.12345"))

    def test_matching_is_case_blind_and_partial(self):
        wire = [{"name": "C.J. Stroud"}, {"name": "Ashton Jeanty"}]
        self.assertEqual([p["name"] for p in ys.matching(wire, "STROUD")],
                         ["C.J. Stroud"])

    def test_an_ambiguous_name_returns_all_of_them(self):
        """So the probe can refuse rather than pick one for you."""
        wire = [{"name": "Josh Allen"}, {"name": "Josh Jacobs"}]
        self.assertEqual(len(ys.matching(wire, "josh")), 2)

    def test_an_empty_name_matches_nobody(self):
        self.assertEqual(ys.matching([{"name": "Anyone"}], ""), [])
        self.assertEqual(ys.matching([{"name": "Anyone"}], None), [])


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


class TheProbeNeverPlacesAClaim(unittest.TestCase):
    """Opening the form is reversible. Placing a claim is not.

    A tool whose whole job is to find out what the page does has no
    business changing anything while it does so, and "it only clicked Add"
    is the sort of thing that is true right up until the markup moves.
    """

    def test_it_clicks_nothing_at_all(self):
        """Once the lookup moved to the API there was nothing left to click.

        The probe opens an address and reads the form. No control is
        touched, which is a stronger promise than clicking only safe ones
        and does not rely on a word list staying accurate.
        """
        source = open("yahoo_submitter.py").read()
        clicks = [l.strip() for l in source.splitlines()
                  if ".click(" in l and not l.strip().startswith("#")]
        self.assertEqual(clicks, [])

    def test_nor_does_it_fill_anything_in(self):
        source = open("yahoo_submitter.py").read()
        for verb in (".fill(", ".select_option(", ".check(", ".press("):
            self.assertNotIn(verb, source)

    def test_it_says_what_it_is_about_to_do(self):
        source = open("yahoo_submitter.py").read()
        self.assertIn("It does not place a claim", source)
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

    def wire_says(self, players):
        import yahoo_client as yc
        self.real_wire = yc.free_agents
        yc.free_agents = lambda *a, **k: players

    def tearDownWire(self):
        import yahoo_client as yc
        if hasattr(self, "real_wire"):
            yc.free_agents = self.real_wire

    def test_with_nothing_listening_it_says_so_and_stops(self):
        self.wire_says([{"name": "C.J. Stroud", "key": "470.p.12345"}])
        ys.submitter.cdp_probe = lambda *a, **k: (False, "ConnectionRefused")

        def explode(*a, **k):
            raise AssertionError("launched a browser of its own")
        ys.submitter.browser_context = explode
        ys.submitter.attach = explode
        try:
            self.assertEqual(ys.do_probe(None, "470.l.715420", "stroud"),
                             ys.submitter.NEEDS_LOGIN)
        finally:
            self.tearDownWire()

    def test_it_never_calls_browser_context_at_all(self):
        """Not even when Chrome IS listening - attach is the only path."""
        self.wire_says([{"name": "C.J. Stroud", "key": "470.p.12345"}])
        ys.submitter.cdp_probe = lambda *a, **k: (True, "Chrome/152")

        def explode(*a, **k):
            raise AssertionError("used browser_context instead of attach")
        ys.submitter.browser_context = explode
        ys.submitter.attach = lambda pw: (_ for _ in ()).throw(
            RuntimeError("attach reached, which is correct"))
        try:
            with self.assertRaises(RuntimeError):
                ys.do_probe(None, "470.l.715420", "stroud")
        finally:
            self.tearDownWire()

    def test_an_unknown_player_never_reaches_a_browser_either(self):
        self.wire_says([{"name": "Someone Else", "key": "470.p.1"}])
        ys.submitter.cdp_probe = lambda *a, **k: (True, "Chrome/152")

        def explode(*a, **k):
            raise AssertionError("opened a browser for a player not free")
        ys.submitter.attach = explode
        try:
            self.assertEqual(ys.do_probe(None, "470.l.715420", "stroud"), 1)
        finally:
            self.tearDownWire()


class TheProbeRefusesBeforeItOpensAnything(unittest.TestCase):

    def test_a_bad_league_key_never_reaches_a_browser(self):
        def explode(*a, **k):
            raise AssertionError("opened a browser for a key it should "
                                 "have refused")
        real = ys.submitter.browser_context
        ys.submitter.browser_context = explode
        try:
            self.assertEqual(ys.do_probe(None, "not-a-key", "x"), 1)
        finally:
            ys.submitter.browser_context = real


if __name__ == "__main__":
    unittest.main(verbosity=2)
