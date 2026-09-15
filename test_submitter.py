#!/usr/bin/env python3
"""Tests for the part of the submitter that decides whether to touch a league.

    python3 test_submitter.py

No browser and no network: the page is a stand-in with the two methods the
check uses. What is being tested is the decision, which is the part that was
wrong - a browser with nobody signed into it was opened, driven at a
logged-out Sleeper, and closed again, so from the outside a window appeared
and vanished with no explanation anywhere.
"""

import unittest

import submitter


SEL = {"home_url": "https://sleeper.com/leagues",
       "signed_out_marker": "text=/log in/i"}


class FakePage:
    """Just enough page to answer the sign-in question."""

    def __init__(self, url="https://sleeper.com/leagues", markers=0,
                 raises=None):
        self.url = url
        self.markers = markers
        self.raises = raises
        self.went_to = None

    def goto(self, url, **_kw):
        if self.raises:
            raise self.raises
        self.went_to = url

    def wait_for_timeout(self, _ms):
        pass

    def locator(self, _selector):
        page = self

        class Found:
            def count(self):
                if isinstance(page.markers, Exception):
                    raise page.markers
                return page.markers
        return Found()


class SignedIn(unittest.TestCase):

    def test_a_live_session_is_left_alone(self):
        ok, why = submitter.signed_in(FakePage(), SEL)
        self.assertTrue(ok)
        self.assertEqual(why, "")

    def test_the_login_screen_is_recognised(self):
        ok, why = submitter.signed_in(FakePage(markers=1), SEL)
        self.assertFalse(ok)
        self.assertIn("log-in screen", why)

    def test_a_redirect_to_login_is_recognised(self):
        ok, why = submitter.signed_in(
            FakePage(url="https://sleeper.com/login"), SEL)
        self.assertFalse(ok)
        self.assertIn("log-in page", why)

    def test_a_browser_that_will_not_open_is_not_a_session(self):
        ok, why = submitter.signed_in(
            FakePage(raises=RuntimeError("boom")), SEL)
        self.assertFalse(ok)
        self.assertIn("could not open", why)

    def test_a_selector_that_no_longer_matches_is_not_evidence(self):
        # Sleeper redesigns; a marker that has rotted must not block a
        # submission that would have worked.
        ok, _why = submitter.signed_in(
            FakePage(markers=RuntimeError("bad selector")), SEL)
        self.assertTrue(ok)

    def test_it_looks_at_the_page_it_was_told_to(self):
        page = FakePage()
        submitter.signed_in(page, SEL)
        self.assertEqual(page.went_to, SEL["home_url"])

    def test_a_missing_marker_falls_back_to_sleeper_itself(self):
        page = FakePage()
        submitter.signed_in(page, {})
        self.assertIn("sleeper.com", page.went_to)


class WhatThePageIsTold(unittest.TestCase):
    """The submitter's report is what the person reads, so it has to say
    what to do rather than point at a log file."""

    def setUp(self):
        self.real = submitter.start_chrome
        submitter.start_chrome = lambda: True
        submitter._note("")

    def tearDown(self):
        submitter.start_chrome = self.real
        submitter._note("")

    def test_being_logged_out_asks_for_a_login_in_words(self):
        detail = submitter.ask_for_login("Sleeper is showing its log-in screen")
        self.assertIn("not signed in", detail)
        self.assertIn("press the button again", detail)

    def test_the_note_survives_to_be_reported(self):
        submitter._note("something happened")
        self.assertEqual(submitter.last_detail("fallback"), "something happened")

    def test_the_fallback_is_used_when_nothing_was_noted(self):
        self.assertEqual(submitter.last_detail("fallback"), "fallback")

    def test_needs_login_is_not_success(self):
        self.assertNotEqual(submitter.NEEDS_LOGIN, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
