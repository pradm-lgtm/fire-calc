#!/usr/bin/env python3
"""Tests for reading .env on the machine that has one.

    python3 test_localenv.py

The rule that matters is precedence: a real environment variable must win,
because on the host the platform's settings are the truth and a stale .env
copied across would silently point the tools at the wrong deployment.
"""

import os
import tempfile
import unittest
from pathlib import Path

import localenv


class Parsing(unittest.TestCase):

    def test_reads_the_shapes_a_hand_written_env_file_has(self):
        got = localenv.parse(
            "# a comment\n"
            "FANTASY_USER=pradm7\n"
            "export FANTASY_API_URL=\"https://example.vercel.app\"\n"
            "QUOTED='keep me'\n"
            "EMPTY=\n"
            "  SPACED = value \n"
            "not a pair\n")
        self.assertEqual(got, {
            "FANTASY_USER": "pradm7",
            "FANTASY_API_URL": "https://example.vercel.app",
            "QUOTED": "keep me",
            "EMPTY": "",
            "SPACED": "value",
        })

    def test_a_value_containing_an_equals_sign_survives(self):
        got = localenv.parse("DATABASE_URL=postgres://u:p==@h/db?sslmode=require")
        self.assertEqual(got["DATABASE_URL"],
                         "postgres://u:p==@h/db?sslmode=require")


class Loading(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / ".env"
        self.addCleanup(self.dir.cleanup)
        for key in ("TEST_ONLY_NEW", "TEST_ONLY_EXISTING"):
            os.environ.pop(key, None)
            self.addCleanup(os.environ.pop, key, None)

    def test_missing_file_is_not_an_error(self):
        self.assertEqual(localenv.load(self.path), [])

    def test_a_real_environment_variable_wins(self):
        os.environ["TEST_ONLY_EXISTING"] = "from the shell"
        self.path.write_text("TEST_ONLY_EXISTING=from the file\n"
                             "TEST_ONLY_NEW=from the file\n")
        added = localenv.load(self.path)
        self.assertEqual(added, ["TEST_ONLY_NEW"])
        self.assertEqual(os.environ["TEST_ONLY_EXISTING"], "from the shell")
        self.assertEqual(os.environ["TEST_ONLY_NEW"], "from the file")


if __name__ == "__main__":
    unittest.main(verbosity=2)
