#!/usr/bin/env python3
"""Tests for the shared API token.

    python3 test_cloud_auth.py

The Mac and the deployment have to hold the same string. Every manual way of
arranging that has failed once already, in ways that all surfaced as the same
401, so the parts that decide "same" are worth pinning down.
"""

import os
import tempfile
import unittest
from pathlib import Path

import cloud_auth as auth


class TokenFile(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / ".env"

    def test_a_missing_file_gets_a_token_written_into_it(self):
        token = auth.token_from_env_file(self.path)
        self.assertTrue(token)
        self.assertIn(token, self.path.read_text())

    def test_asking_twice_gives_the_same_token(self):
        # Generating a new one on each call is the failure this replaces:
        # the two sides end up holding different strings.
        first = auth.token_from_env_file(self.path)
        self.assertEqual(auth.token_from_env_file(self.path), first)

    def test_an_existing_token_is_left_alone(self):
        self.path.write_text("FANTASY_USER=someone\n"
                             "FANTASY_API_TOKEN=already-here\n")
        self.assertEqual(auth.token_from_env_file(self.path), "already-here")

    def test_other_values_in_the_file_survive(self):
        self.path.write_text("FANTASY_PASSWORD=keep\n")
        auth.token_from_env_file(self.path)
        self.assertIn("FANTASY_PASSWORD=keep", self.path.read_text())


class Complaints(unittest.TestCase):

    def setUp(self):
        self.old = os.environ.get("FANTASY_API_TOKEN")
        self.addCleanup(lambda: os.environ.__setitem__(
            "FANTASY_API_TOKEN", self.old) if self.old is not None
            else os.environ.pop("FANTASY_API_TOKEN", None))

    def test_a_mismatch_names_both_sides_without_revealing_either(self):
        os.environ["FANTASY_API_TOKEN"] = "server-side"
        said = auth.token_complaint("Bearer caller-side")
        self.assertIn(auth.fingerprint("server-side"), said)
        self.assertIn(auth.fingerprint("caller-side"), said)
        self.assertNotIn("server-side", said)
        self.assertNotIn("caller-side", said)

    def test_the_same_token_on_both_sides_is_accepted(self):
        os.environ["FANTASY_API_TOKEN"] = "shared"
        self.assertTrue(auth.check_api_token("Bearer shared"))

    def test_a_missing_header_is_reported_as_missing(self):
        os.environ["FANTASY_API_TOKEN"] = "shared"
        self.assertIn("no bearer token", auth.token_complaint(None))

    def test_no_token_on_the_server_says_so(self):
        os.environ.pop("FANTASY_API_TOKEN", None)
        self.assertIn("server has no", auth.token_complaint("Bearer anything"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
