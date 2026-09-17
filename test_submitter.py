#!/usr/bin/env python3
"""Tests for the part of the submitter that decides whether to touch a league.

    python3 test_submitter.py

No browser and no network: the page is a stand-in with the two methods the
check uses. What is being tested is the decision, which is the part that was
wrong - a browser with nobody signed into it was opened, driven at a
logged-out Sleeper, and closed again, so from the outside a window appeared
and vanished with no explanation anywhere.
"""

import contextlib
import io
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


class Port9222(unittest.TestCase):
    """Whether Chrome is listening, and saying which answer we got.

    The probe used to return a bare True/False through urlopen, which honours
    http_proxy - so on a machine with a proxy set, "is Chrome listening on
    127.0.0.1?" was a question put to the proxy. A wrong yes sent the login
    helper down the attach path, where it printed an instruction to start
    Chrome "as above" under nothing at all.
    """

    def probe(self, body=None, error=None):
        import urllib.request
        real = urllib.request.build_opener

        class Response:
            def read(self, _n=None):
                return body.encode()

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        class Opener:
            def open(self, _url, timeout=None):
                if error:
                    raise error
                return Response()

        urllib.request.build_opener = lambda *_a, **_k: Opener()
        try:
            return submitter.cdp_probe()
        finally:
            urllib.request.build_opener = real

    def test_a_real_chrome_is_named(self):
        alive, saw = self.probe(body='{"Browser": "Chrome/141.0.0.0"}')
        self.assertTrue(alive)
        self.assertIn("Chrome/141", saw)

    def test_nothing_listening_reports_the_error(self):
        alive, saw = self.probe(error=OSError("Connection refused"))
        self.assertFalse(alive)
        self.assertIn("Connection refused", saw)

    def test_something_that_is_not_chrome_is_not_a_yes(self):
        # A proxy answering for localhost, which is the whole reason the
        # probe stopped trusting a bare 200.
        alive, saw = self.probe(body="<html>proxy error</html>")
        self.assertFalse(alive)
        self.assertIn("not Chrome", saw)

    def test_the_old_boolean_still_works_for_its_callers(self):
        import urllib.request
        real = urllib.request.build_opener
        urllib.request.build_opener = lambda *_a, **_k: (_ for _ in ()).throw(
            OSError("nope"))
        try:
            self.assertFalse(submitter.cdp_alive())
        finally:
            urllib.request.build_opener = real

    def test_the_command_to_run_by_hand_is_a_real_command(self):
        hint = submitter.launch_hint()
        self.assertIn("--remote-debugging-port=9222", hint)
        self.assertIn("--user-data-dir", hint)

    def test_a_long_reply_is_read_whole(self):
        # Chrome's /json/version runs past 400 bytes. Reading a prefix of it
        # and parsing that was reporting a real Chrome as "not Chrome".
        body = ('{"Browser": "Chrome/152.0.7977.83", "Protocol-Version":'
                ' "1.3", "User-Agent": "' + "x" * 500 + '",'
                ' "V8-Version": "', )
        body = (body[0] + '13.1", "WebKit-Version": "537.36",'
                ' "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/'
                + "y" * 200 + '"}')
        self.assertGreater(len(body), 400)
        alive, saw = self.probe(body=body)
        self.assertTrue(alive)
        self.assertIn("Chrome/152", saw)

    def test_the_flag_chrome_has_required_since_v111_is_passed(self):
        # Without it the port answers /json/version and then refuses every
        # attach, which reads as "something is there but will not talk".
        self.assertIn("--remote-allow-origins=*", submitter.CHROME_FLAGS)

    def test_the_printed_command_survives_a_shell(self):
        # A bare * in a shell is a glob over the current directory.
        self.assertIn("--remote-allow-origins='*'", submitter.launch_hint())


class WhichTab(unittest.TestCase):
    """Finding the Sleeper tab among whatever else is open.

    Reporting "1 tab(s) open" to someone who cannot see a window is not
    help. The login step opens or surfaces a Sleeper tab so there is
    something to look at.
    """

    def test_it_finds_sleeper_among_others(self):
        self.assertEqual(submitter.sleeper_tab(
            ["https://news.ycombinator.com", "https://sleeper.com/leagues"]), 1)

    def test_the_first_one_wins(self):
        self.assertEqual(submitter.sleeper_tab(
            ["https://sleeper.com/draft", "https://sleeper.com/leagues"]), 0)

    def test_no_sleeper_tab_is_none_not_zero(self):
        self.assertIsNone(submitter.sleeper_tab(["about:blank"]))

    def test_an_empty_browser_is_none(self):
        self.assertIsNone(submitter.sleeper_tab([]))

    def test_a_tab_with_no_url_does_not_explode(self):
        self.assertIsNone(submitter.sleeper_tab([None, ""]))

    def test_a_lookalike_domain_is_not_sleeper(self):
        self.assertIsNone(submitter.sleeper_tab(["https://sleeperbot.io"]))


class ChromeEndpoints(unittest.TestCase):
    """Driving the tab through Chrome rather than through Playwright.

    A page created on an attached Playwright browser belongs to that
    connection, and closing the connection can take the tab with it - which
    would shut the very window opened for someone to sign in. Chrome's own
    HTTP interface hands back a tab that belongs to Chrome.
    """

    def setUp(self):
        self.calls = []
        self.answer = None
        self.real = submitter.cdp_call

        def fake(path, method="GET", timeout=5):
            self.calls.append((method, path))
            return self.answer(path, method) if callable(self.answer) \
                else self.answer
        submitter.cdp_call = fake

    def tearDown(self):
        submitter.cdp_call = self.real

    def test_only_pages_count_as_tabs(self):
        self.answer = [
            {"id": "a", "type": "page", "url": "https://sleeper.com"},
            {"id": "b", "type": "service_worker", "url": "https://x.dev/sw.js"},
            {"id": "c", "type": "background_page", "url": "chrome://ext"},
        ]
        self.assertEqual(submitter.cdp_tabs(), [("a", "https://sleeper.com")])

    def test_a_browser_that_answers_nonsense_has_no_tabs(self):
        self.answer = "not json at all"
        self.assertEqual(submitter.cdp_tabs(), [])

    def test_a_browser_that_does_not_answer_has_no_tabs(self):
        self.answer = None
        self.assertEqual(submitter.cdp_tabs(), [])

    def test_opening_a_tab_tries_the_modern_verb_first(self):
        self.answer = lambda _p, method: ({"id": "new1"} if method == "PUT"
                                          else None)
        self.assertEqual(submitter.cdp_open("https://sleeper.com"), "new1")
        self.assertEqual(self.calls[0][0], "PUT")

    def test_opening_falls_back_for_older_chrome(self):
        self.answer = lambda _p, method: ({"id": "new2"} if method == "GET"
                                          else None)
        self.assertEqual(submitter.cdp_open("https://sleeper.com"), "new2")
        self.assertEqual([m for m, _p in self.calls], ["PUT", "GET"])

    def test_the_url_survives_into_the_request(self):
        self.answer = {"id": "x"}
        submitter.cdp_open("https://sleeper.com")
        self.assertIn("https://sleeper.com", self.calls[0][1])

    def test_a_refusal_to_open_is_reported_not_guessed(self):
        self.answer = None
        self.assertIsNone(submitter.cdp_open("https://sleeper.com"))


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
        # Swallow its console output: the migration script tails the last few
        # lines of each test file to report the result, and instructions
        # meant for a person pushed the result itself off the bottom.
        with contextlib.redirect_stdout(io.StringIO()):
            detail = submitter.ask_for_login(
                "Sleeper is showing its log-in screen")
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
