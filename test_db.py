#!/usr/bin/env python3
"""Tests for splitting a SQL script into statements.

    python3 test_db.py

sqlite3 runs a whole script itself, so this code path exists only for
Postgres and only runs on a deployment. One semicolon inside a prose comment
broke every hosted run while every local run stayed green, which is exactly
the kind of difference a test has to cover instead of a deploy.
"""

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
        got = db.statements(store.SCHEMA)
        self.assertEqual(len(got), 8)
        for statement in got:
            self.assertTrue(statement.upper().startswith("CREATE"), statement)

    def test_no_statement_carries_a_comment_into_postgres(self):
        for statement in db.statements(store.SCHEMA):
            self.assertNotIn("--", statement)


if __name__ == "__main__":
    unittest.main(verbosity=2)
