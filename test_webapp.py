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


class Freshness(unittest.TestCase):
    """How old a set of verdicts is, and when to work them out again."""

    def setUp(self):
        from datetime import datetime, timedelta, timezone
        self.now = datetime.now(timezone.utc)
        self.timedelta = timedelta

    def check(self, **kw):
        return {"created_at": (self.now - self.timedelta(**kw)).isoformat()}

    def test_age_reads_a_stored_timestamp(self):
        age = webapp.age_of(self.check(hours=2))
        self.assertAlmostEqual(age.total_seconds(), 7200, delta=5)

    def test_nothing_stored_has_no_age(self):
        self.assertIsNone(webapp.age_of(None))
        self.assertIsNone(webapp.age_of({"created_at": None}))
        self.assertIsNone(webapp.age_of({"created_at": "not a date"}))

    def test_a_timestamp_without_a_zone_is_read_as_utc(self):
        # Postgres and SQLite disagree about whether one comes back with a
        # zone, and treating a naive one as local time made checks look
        # hours old the moment they were written.
        naive = (self.now - self.timedelta(minutes=5)).replace(tzinfo=None)
        age = webapp.age_of({"created_at": naive.isoformat()})
        self.assertAlmostEqual(age.total_seconds(), 300, delta=5)

    def test_age_is_described_in_the_largest_useful_unit(self):
        self.assertEqual(webapp.said_ago(None), "just now")
        self.assertEqual(webapp.said_ago(self.timedelta(seconds=30)), "just now")
        self.assertEqual(webapp.said_ago(self.timedelta(minutes=20)),
                         "20 minutes ago")
        self.assertEqual(webapp.said_ago(self.timedelta(hours=1)), "1 hour ago")
        self.assertEqual(webapp.said_ago(self.timedelta(hours=5)), "5 hours ago")
        self.assertEqual(webapp.said_ago(self.timedelta(days=2)), "2 days ago")


class LineupPage(unittest.TestCase):
    """What the page puts first, and what it folds away."""

    def rows(self):
        def row(**kw):
            base = dict(league_id="1", league_name="OTG", slot="QB",
                        position=0, verdict="GREEN", player_id="1",
                        player_name="A", rank_text="QB1", better_name=None,
                        detail="", role="starter", pos="QB", matchup="vs PHI",
                        projection=10.0)
            base.update(kw)
            return base
        return [
            row(league_id="2", league_name="LEHG", player_id="9",
                player_name="Quiet Starter"),
            row(verdict="YELLOW", player_id="1", player_name="Close Call",
                better_name="Better QB", detail="consensus prefers Better QB"),
            row(verdict="RED", slot="FLEX", position=1, player_id="3",
                player_name="Wrong Call", pos="RB", better_name="Better RB"),
            row(slot="BN", verdict="BENCH", role="bench", player_id="4",
                player_name="Better QB", projection=20.0),
            row(slot="BN", verdict="BENCH", role="bench", player_id="5",
                player_name="Better RB", pos="RB", projection=15.0),
        ]

    def render(self):
        import store as st
        conn = st.connect(":memory:")
        check_id = st.start_lineup_check(conn, "2026", 1, ["page"])
        for r in self.rows():
            st.add_lineup_flag(conn, check_id, **r)
        return webapp.render_lineup(conn).decode()

    def test_red_comes_before_yellow(self):
        html = self.render()
        self.assertLess(html.index("Wrong Call"), html.index("Close Call"))

    def test_a_flagged_league_outranks_a_quiet_one(self):
        # The complaint that started this: the league with nothing wrong
        # sorted first, so the two decisions were below a screen of green.
        html = self.render()
        self.assertLess(html.index("Wrong Call"), html.index("Quiet Starter"))

    def test_a_quiet_league_is_folded_shut(self):
        html = self.render()
        fold = html[html.index("Everything else"):]
        self.assertIn("<details class='fold'>", fold)

    def test_a_decision_shows_the_bench_players_that_could_take_the_slot(self):
        html = self.render()
        card = html[html.index("Wrong Call"):html.index("Close Call")]
        self.assertIn("Better RB", card)      # a running back can play flex
        self.assertNotIn("Better QB", card)   # a quarterback cannot

    def test_the_opponent_and_projection_are_shown(self):
        html = self.render()
        self.assertIn("vs PHI", html)
        self.assertIn("10 proj", html)

    def test_a_missing_projection_leaves_the_line_alone(self):
        self.assertEqual(webapp.where_and_points(
            {"matchup": "at LV", "projection": None}), "at LV")
        self.assertEqual(webapp.where_and_points(
            {"matchup": None, "projection": None}), "")

    def test_a_defence_gets_its_team_badge_not_a_headshot(self):
        self.assertIn("team_logos", webapp.headshot(
            {"player_id": "PIT", "pos": "DEF"}))
        self.assertIn("players/1234", webapp.headshot(
            {"player_id": "1234", "pos": "RB"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
