#!/usr/bin/env python3
"""The one network call everything else is built on.

Every part of this reads Sleeper first: the week, the rosters, the player
list. A single fetch failing used to end the run, which matters because the
run that matters is unattended - launchd, once a week - and a transient
fault there costs the week's proposals rather than a retry.
"""

import unittest
import urllib.error
import urllib.request

import sleeper_client as sc


class Answer:
    def __init__(self, body=b'{"week": 3}'):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


class Retrying(unittest.TestCase):

    def setUp(self):
        self.real_open = urllib.request.urlopen
        self.real_sleep = sc.time.sleep
        self.waits = []
        sc.time.sleep = self.waits.append
        self.calls = []

    def tearDown(self):
        urllib.request.urlopen = self.real_open
        sc.time.sleep = self.real_sleep

    def answering_after(self, failures, error=None):
        error = error or urllib.error.URLError(
            "nodename nor servname provided, or not known")

        def fetch(_req, timeout=None):
            self.calls.append(1)
            if len(self.calls) <= failures:
                raise error
            return Answer()
        urllib.request.urlopen = fetch

    def test_a_name_that_will_not_resolve_is_tried_again(self):
        self.answering_after(2)
        self.assertEqual(sc.get("/state/nfl"), {"week": 3})
        self.assertEqual(len(self.calls), 3)

    def test_the_waits_get_longer(self):
        self.answering_after(2)
        sc.get("/state/nfl")
        self.assertEqual(self.waits, sorted(self.waits))
        self.assertEqual(len(set(self.waits)), len(self.waits))

    def test_it_gives_up_rather_than_hammering(self):
        self.answering_after(99)
        with self.assertRaises(sc.SleeperError):
            sc.get("/state/nfl")
        self.assertEqual(len(self.calls), sc.TRIES)

    def test_giving_up_says_it_is_this_machine(self):
        """The message people read at 8:30 on a Monday, in a log."""
        self.answering_after(99)
        try:
            sc.get("/state/nfl")
        except sc.SleeperError as exc:
            self.assertIn("network or DNS", str(exc))
            self.assertIn("not the API", str(exc))

    def test_a_server_error_is_retried(self):
        self.answering_after(2, urllib.error.HTTPError(
            "u", 503, "busy", {}, None))
        self.assertEqual(sc.get("/state/nfl"), {"week": 3})

    def test_being_throttled_is_retried(self):
        self.answering_after(1, urllib.error.HTTPError(
            "u", 429, "slow down", {}, None))
        self.assertEqual(sc.get("/state/nfl"), {"week": 3})

    def test_a_404_is_an_answer_and_is_not_retried(self):
        import io
        self.answering_after(99, urllib.error.HTTPError(
            "u", 404, "gone", {}, io.BytesIO(b"")))
        with self.assertRaises(sc.SleeperError):
            sc.get("/state/nfl")
        self.assertEqual(len(self.calls), 1)

    def test_a_bad_request_will_be_bad_next_time_too(self):
        import io
        self.answering_after(99, urllib.error.HTTPError(
            "u", 400, "nope", {}, io.BytesIO(b"")))
        with self.assertRaises(sc.SleeperError):
            sc.get("/state/nfl")
        self.assertEqual(len(self.calls), 1)

    def test_a_first_try_that_works_waits_for_nothing(self):
        self.answering_after(0)
        self.assertEqual(sc.get("/state/nfl"), {"week": 3})
        self.assertEqual(self.calls, [1])
        self.assertEqual(self.waits, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
