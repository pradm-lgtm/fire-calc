#!/usr/bin/env python3
"""Tests for splitting a SQL script into statements.

    python3 test_db.py

sqlite3 runs a whole script itself, so this code path exists only for
Postgres and only runs on a deployment. One semicolon inside a prose comment
broke every hosted run while every local run stayed green, which is exactly
the kind of difference a test has to cover instead of a deploy.
"""

import os
import unittest

import db
import store


class Splitting(unittest.TestCase):

    def test_a_semicolon_inside_a_comment_does_not_end_a_statement(self):
        got = db.statements(
            "-- one thing; and another\n"
            "CREATE TABLE a (id INTEGER);\n")
        self.assertEqual(got, ["CREATE TABLE a (id INTEGER)"])

    def test_a_comment_only_script_yields_nothing_to_run(self):
        # Postgres rejects an empty query, so a fragment that is only a
        # comment must never reach it.
        self.assertEqual(db.statements("-- just a note;\n-- and more\n"), [])

    def test_a_semicolon_inside_a_string_is_kept(self):
        got = db.statements("INSERT INTO a VALUES ('x; y');")
        self.assertEqual(got, ["INSERT INTO a VALUES ('x; y')"])

    def test_an_escaped_quote_does_not_end_the_string(self):
        got = db.statements("INSERT INTO a VALUES ('it''s; fine');")
        self.assertEqual(got, ["INSERT INTO a VALUES ('it''s; fine')"])

    def test_trailing_text_without_a_semicolon_still_counts(self):
        self.assertEqual(db.statements("SELECT 1"), ["SELECT 1"])


class TheRealSchema(unittest.TestCase):

    def test_every_statement_is_something_postgres_can_run(self):
        # Counting statements made this test fail every time a table was
        # added, which says nothing about whether the schema is valid.
        got = db.statements(store.SCHEMA)
        self.assertTrue(got)
        for statement in got:
            self.assertTrue(statement.upper().startswith("CREATE"), statement)

    def test_every_table_the_code_writes_to_is_created(self):
        made = " ".join(db.statements(store.SCHEMA))
        for table in ("runs", "proposals", "events", "lineup_checks",
                      "lineup_flags", "lineup_done"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table} ", made)

    def test_no_statement_carries_a_comment_into_postgres(self):
        for statement in db.statements(store.SCHEMA):
            self.assertNotIn("--", statement)


class DroppingOptions(unittest.TestCase):
    """A URL written for one client version reaching an older one."""

    URL = "postgresql://u:p@h/db?sslmode=require&channel_binding=require"

    def test_the_option_goes_and_the_rest_stays(self):
        self.assertEqual(db._without(self.URL, "channel_binding"),
                         "postgresql://u:p@h/db?sslmode=require")

    def test_dropping_the_first_option_leaves_a_valid_url(self):
        url = "postgresql://u:p@h/db?channel_binding=require&sslmode=require"
        self.assertEqual(db._without(url, "channel_binding"),
                         "postgresql://u:p@h/db?sslmode=require")

    def test_dropping_the_only_option_leaves_no_question_mark(self):
        self.assertEqual(db._without("postgresql://u:p@h/db?channel_binding=x",
                                     "channel_binding"),
                         "postgresql://u:p@h/db")

    def test_an_escaped_password_is_not_re_encoded(self):
        url = "postgresql://u:pa%40ss@h/db?sslmode=require&channel_binding=x"
        self.assertIn("pa%40ss", db._without(url, "channel_binding"))

    def test_an_unknown_option_is_retried_without_it(self):
        class FakeError(Exception):
            pass

        class FakeDriver:
            Error = FakeError

            def __init__(self):
                self.seen = []

            def connect(self, url):
                self.seen.append(url)
                if "channel_binding" in url:
                    raise FakeError('invalid connection option '
                                    '"channel_binding"')
                return "connected"

        driver = FakeDriver()
        self.assertEqual(db._connect(driver, self.URL), "connected")
        self.assertEqual(driver.seen[-1], "postgresql://u:p@h/db?sslmode=require")

    def test_any_other_failure_is_raised_rather_than_retried(self):
        class FakeError(Exception):
            pass

        class FakeDriver:
            Error = FakeError

            def connect(self, url):
                raise FakeError("password authentication failed")

        with self.assertRaises(FakeError):
            db._connect(FakeDriver(), self.URL)


class WhichDatabase(unittest.TestCase):
    """Which backend a connection uses, and when that choice is not open."""

    def setUp(self):
        self.had = os.environ.get("DATABASE_URL")
        self.addCleanup(self.restore)

    def restore(self):
        if self.had is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self.had

    def test_in_memory_is_sqlite_even_with_a_postgres_url_set(self):
        # Any machine that talks to the deployment has DATABASE_URL set, so
        # without this every test write on that machine went to the real
        # database.
        os.environ["DATABASE_URL"] = "postgres://pretend/nowhere"
        self.assertEqual(db.Connection(":memory:").kind, db.SQLITE)

    def test_a_file_path_still_follows_the_environment(self):
        os.environ["DATABASE_URL"] = "postgres://pretend/nowhere"
        self.assertEqual(db.backend(), db.POSTGRES)

    def test_no_url_means_a_local_file(self):
        os.environ.pop("DATABASE_URL", None)
        self.assertEqual(db.backend(), db.SQLITE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
