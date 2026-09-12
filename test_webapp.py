#!/usr/bin/env python3
"""Tests for which page a request is actually asking for.

    python3 test_webapp.py

Hosting rewrites every URL to the single function that serves the app, so
the path the function receives is not the path the browser asked for. Every
route 404s when that goes wrong, which is what it did.
"""

import unittest

import webapp


def route(path):
    handler = object.__new__(webapp.Handler)
    handler.path = path
    return handler.route()


class Routing(unittest.TestCase):

    def test_a_direct_request_is_left_alone(self):
        self.assertEqual(route("/"), "/")
        self.assertEqual(route("/lineup"), "/lineup")
        self.assertEqual(route("/api/claims?include=submitted"), "/api/claims")

    def test_a_rewritten_request_reports_the_original_path(self):
        self.assertEqual(route("/api/index?__path=/"), "/")
        self.assertEqual(route("/api/index?__path=/lineup"), "/lineup")
        self.assertEqual(route("/api/index?__path=/api/lineup"), "/api/lineup")

    def test_the_rest_of_the_query_still_arrives(self):
        self.assertEqual(
            route("/api/index?__path=/api/claims&include=submitted"),
            "/api/claims")

    def test_the_function_path_alone_means_the_front_page(self):
        # Without the original path there is nothing else it can be, and a
        # 404 on the front page reads as the whole site being broken.
        self.assertEqual(route("/api/index"), "/")

    def test_a_path_that_is_not_a_route_is_still_not_a_route(self):
        self.assertEqual(route("/api/index?__path=/nope"), "/nope")


class Scrubbing(unittest.TestCase):
    """An error message must not carry the password out of the process."""

    def test_a_connection_string_loses_its_credentials(self):
        got = webapp.scrub("could not connect to "
                           "postgresql://owner:npg_secret@ep-x.neon.tech/db")
        self.assertNotIn("npg_secret", got)
        self.assertIn("ep-x.neon.tech/db", got)

    def test_ordinary_text_is_left_alone(self):
        self.assertEqual(webapp.scrub("ModuleNotFoundError: no module psycopg2"),
                         "ModuleNotFoundError: no module psycopg2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
