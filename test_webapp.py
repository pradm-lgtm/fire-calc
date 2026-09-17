#!/usr/bin/env python3
"""Tests for which page a request is actually asking for.

    python3 test_webapp.py

Hosting rewrites every URL to the single function that serves the app, so
the path the function receives is not the path the browser asked for. Every
route 404s when that goes wrong, which is what it did.
"""

import unittest

import store as st
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
        fold = html[html.index("Full lineups"):]
        self.assertIn("<details class='fold'>", fold)

    def test_a_decision_shows_the_bench_players_that_could_take_the_slot(self):
        html = self.render()
        start = html.index("Wrong Call")
        card = html[start:html.index("</article>", start)]
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


class Scales(unittest.TestCase):
    """Which ranking a line shows depends on the slot it is answering."""

    ROW = {"rank_text": "RB31", "overall_text": "overall 66"}

    def test_a_flex_slot_is_answered_on_the_cross_positional_scale(self):
        self.assertEqual(webapp.rank_label(self.ROW, True), "overall 66")

    def test_a_fixed_slot_is_answered_within_its_position(self):
        self.assertEqual(webapp.rank_label(self.ROW, False), "RB31")

    def test_a_player_with_no_overall_rank_falls_back(self):
        self.assertEqual(webapp.rank_label(
            {"rank_text": "K3", "overall_text": "unranked"}, True), "K3")
        self.assertEqual(webapp.rank_label(
            {"rank_text": "K3", "overall_text": None}, True), "K3")


class Locking(unittest.TestCase):
    """A slot stops being a decision once its game has begun."""

    def rows(self):
        def row(**kw):
            base = dict(league_id="1", league_name="OTG", slot="FLEX",
                        position=0, verdict="RED", player_id="1",
                        player_name="Playing Now", rank_text="RB31",
                        overall_text="overall 66", better_name=None,
                        detail="he is Out — he will not play", role="starter",
                        pos="RB", matchup="at LV", projection=4.0, locked=1)
            base.update(kw)
            return base
        return [row(),
                row(position=1, player_id="2", player_name="Still Choosable",
                    locked=0)]

    def render(self):
        import store as st
        conn = st.connect(":memory:")
        check_id = st.start_lineup_check(conn, "2026", 2, ["page"])
        for r in self.rows():
            st.add_lineup_flag(conn, check_id, **r)
        return webapp.render_lineup(conn).decode()

    def test_a_started_game_is_shown_but_not_counted_as_open(self):
        # It stays on the page - the call was real - but the chance to act
        # on it has passed, so it is not something still to do.
        html = self.render()
        cards = html[html.index("Fix these"):html.index("Full lineups")]
        self.assertIn("Still Choosable", cards)
        self.assertIn("Playing Now", cards)
        self.assertIn("1 still open", html)
        self.assertIn("Kickoff has passed", html)

    def test_the_count_says_how_many_are_already_playing(self):
        self.assertIn("already playing", self.render())

    def test_a_started_player_is_still_listed_with_a_badge(self):
        html = self.render()
        self.assertIn("Playing Now", html)
        self.assertIn("playing now", html)


class Redesign(unittest.TestCase):
    """The page has to answer "what do I do" before it explains itself."""

    def build(self):
        import store as st
        conn = st.connect(":memory:")
        check_id = st.start_lineup_check(conn, "2026", 1, ["page"])

        def row(**kw):
            base = dict(league_id="2", league_name="LEHG", slot="WR",
                        position=0, verdict="GREEN", player_id="1",
                        player_name="Quiet Guy (BUF WR)", rank_text="WR11",
                        overall_text="overall 30", better_name=None, detail="",
                        role="starter", pos="WR", matchup="at IND",
                        projection=12.8, locked=0)
            base.update(kw)
            return base

        for r in [
            row(verdict="YELLOW", slot="FLEX", position=1, player_id="11",
                player_name="Close Guy (TB RB)", pos="RB",
                better_name="Spears (TEN RB)",
                detail="Analysts rank Spears three spots higher."),
            row(verdict="RED", player_id="10", player_name="Hurt Guy (BAL WR)",
                better_name="Robinson (TEN WR)",
                detail="Hurt Guy is out this week."),
            row(slot="BN", verdict="BENCH", role="bench", player_id="20",
                player_name="Robinson (TEN WR)"),
            row(slot="BN", verdict="BENCH", role="bench", player_id="22",
                player_name="Spears (TEN RB)", pos="RB"),
        ]:
            st.add_lineup_flag(conn, check_id, **r)
        return conn

    def html(self, conn=None):
        return webapp.render_lineup(conn or self.build()).decode()

    def test_the_headline_is_the_action(self):
        self.assertIn("<h3>Start Robinson over Hurt Guy</h3>", self.html())

    def test_urgent_calls_come_before_marginal_ones(self):
        html = self.html()
        self.assertLess(html.index("Fix these"), html.index("Close calls"))
        self.assertLess(html.index("Hurt Guy"), html.index("Close Guy"))

    def test_the_two_tiers_look_different(self):
        html = self.html()
        self.assertIn("class='call urgent'", html)
        self.assertIn("class='call close'", html)

    def test_the_player_to_start_is_the_row_that_stands_out(self):
        html = self.html()
        card = html[html.index("Start Robinson"):html.index("Close calls")]
        self.assertIn("class='player pick'", card)
        self.assertIn(">Start<", card)
        self.assertIn("Instead of", card)

    def test_nothing_shouts_in_capitals(self):
        # Tracked-out capitals read as template chrome, not information.
        self.assertNotIn("text-transform:uppercase", self.html())

    def test_the_tabs_are_not_prefixed_with_the_site_name(self):
        html = self.html()
        self.assertIn(">Waivers<", html)
        self.assertNotIn(">Spike — waivers<", html)

    def test_how_it_ranks_is_a_footnote_not_a_headline(self):
        html = self.html()
        note = html[html.index("class='foot'"):]
        self.assertIn("consensus rankings", note)
        self.assertNotIn("not projections", html[:html.index("Fix these")])

    def test_marking_one_done_moves_it_out_of_the_way(self):
        import store as st
        conn = self.build()
        st.settle(conn, "2026", 1, "2", "WR", "10")
        html = self.html(conn)
        self.assertIn("Settled", html)
        self.assertNotIn("Start Robinson over", html)
        self.assertIn("Undo", html)

    def test_a_settled_call_stays_settled_across_a_new_check(self):
        # Every refresh writes new rows; keying dismissals to a check would
        # resurrect all of them, which is the thing this is meant to stop.
        import store as st
        conn = self.build()
        st.settle(conn, "2026", 1, "2", "WR", "10")
        later = st.start_lineup_check(conn, "2026", 1, ["page"])
        st.add_lineup_flag(conn, later, league_id="2", league_name="LEHG",
                           slot="WR", position=0, verdict="RED",
                           player_id="10", player_name="Hurt Guy (BAL WR)",
                           better_name="Robinson (TEN WR)", role="starter",
                           pos="WR", detail="Hurt Guy is out this week.")
        self.assertNotIn("Start Robinson over", self.html(conn))


class AfterKickoff(unittest.TestCase):
    """What a call said before the game is what it goes on saying."""

    def starter(self, **kw):
        base = dict(league_id="2", league_name="LEHG", slot="WR", position=0,
                    verdict="RED", player_id="10",
                    player_name="Zay Flowers (BAL WR)", rank_text="WR11",
                    overall_text="overall 30",
                    better_name="Robinson (TEN WR)", role="starter", pos="WR",
                    matchup="at IND", projection=12.8, locked=0,
                    detail="Analysts rank Robinson four spots higher.")
        base.update(kw)
        return base

    BENCH = dict(league_id="2", league_name="LEHG", slot="BN", position=0,
                 verdict="BENCH", player_id="20",
                 player_name="Robinson (TEN WR)", rank_text="WR39",
                 overall_text="overall 88", role="bench", pos="WR",
                 matchup="at NYJ", projection=9.1, locked=0)

    def played(self):
        """A check taken before kickoff, then one taken during the game."""
        import store as st
        conn = st.connect(":memory:")
        st.write_lineup_check(conn, "2026", 3, ["p"],
                              [self.starter(), dict(self.BENCH)])
        st.write_lineup_check(conn, "2026", 3, ["p"], [
            self.starter(locked=1, better_name="Someone Else (NYJ WR)",
                         detail="Flowers is out this week."),
            dict(self.BENCH, locked=1)])
        return webapp.render_lineup(conn).decode()

    def test_the_call_is_still_there(self):
        # Hiding it claimed the lineup matched consensus, when what really
        # happened is that the chance to change it passed.
        html = self.played()
        self.assertIn("The call was Robinson over Zay Flowers", html)
        self.assertNotIn("Every starter matches", html)

    def test_an_injury_during_the_game_does_not_rewrite_it(self):
        html = self.played()
        self.assertIn("four spots higher", html)
        self.assertNotIn("Someone Else", html)

    def test_it_reads_as_closed_rather_than_outstanding(self):
        html = self.played()
        self.assertIn(" shut'", html)
        self.assertIn("Kickoff has passed", html)
        self.assertIn("nothing to do", html)

    def test_a_closed_call_is_never_phrased_as_an_instruction(self):
        # "Start Robinson over Flowers" above "Kickoff has passed" tells you
        # to do something and then that you cannot.
        html = self.played()
        self.assertIn("The call was Robinson over Zay Flowers", html)
        self.assertNotIn("Start Robinson over", html)

    def test_a_closed_call_does_not_sit_under_an_imperative_heading(self):
        html = self.played()
        self.assertIn("Already played", html)
        self.assertNotIn("Fix these", html)

    def test_a_closed_call_offers_no_start_marker(self):
        html = self.played()
        card = html[html.index("The call was"):]
        self.assertNotIn(">Start<", card[:card.index("</article>")])

    def test_both_kinds_can_appear_at_once(self):
        import store as st
        conn = st.connect(":memory:")
        st.write_lineup_check(conn, "2026", 3, ["p"],
                              [self.starter(), dict(self.BENCH)])
        st.write_lineup_check(conn, "2026", 3, ["p"], [
            self.starter(locked=1),
            self.starter(position=1, player_id="11",
                         player_name="Open Guy (KC WR)", locked=0),
            dict(self.BENCH, locked=1)])
        html = webapp.render_lineup(conn).decode()
        self.assertLess(html.index("Fix these"), html.index("Already played"))
        self.assertIn("Start Robinson over Open Guy", html)
        self.assertIn("The call was Robinson over Zay Flowers", html)

    def test_a_closed_call_offers_no_action(self):
        html = self.played()
        card = html[html.index("The call was"):]
        self.assertNotIn("Mark as done", card[:card.index("</article>")])

    def test_an_open_call_is_unaffected(self):
        import store as st
        conn = st.connect(":memory:")
        st.write_lineup_check(conn, "2026", 3, ["p"],
                              [self.starter(), dict(self.BENCH)])
        html = webapp.render_lineup(conn).decode()
        self.assertIn("1 still open", html)
        self.assertIn("Mark as done", html)


class SubmitButton(unittest.TestCase):
    """Asking the Mac to place what you approved."""

    def ready(self):
        import store as st
        conn = st.connect(":memory:")
        run = st.start_run(conn, "2026", 3, ["ESPN"])
        pid = st.add_proposal(conn, run, league_id="1", league_name="OTG",
                              add_player_id="9",
                              add_player_name="Someone (LV RB)",
                              add_position="RB", bid=6, max_bid=100,
                              consensus=2, sources=["https://espn.com/x"],
                              rationale="RB is deep")
        st.decide(conn, pid, st.APPROVED, bid=6)
        return conn

    def test_the_button_appears_only_with_something_to_place(self):
        import store as st
        conn = self.ready()
        self.assertIn("Place them in Sleeper now",
                      webapp.render(conn).decode())
        empty = st.connect(":memory:")
        st.start_run(empty, "2026", 3, ["ESPN"])
        self.assertNotIn("Place them in Sleeper now",
                         webapp.render(empty).decode())

    def test_pressing_it_replaces_the_button_with_what_it_is_waiting_for(self):
        import store as st
        conn = self.ready()
        st.ask_to_submit(conn)
        html = webapp.render(conn).decode()
        self.assertNotIn("Place them in Sleeper now", html)
        self.assertIn("awake, with Chrome open and signed in", html)

    def test_a_finished_run_reports_back_on_the_page(self):
        import store as st
        conn = self.ready()
        request = st.ask_to_submit(conn)
        st.claim_submit_request(conn, request)
        st.finish_submit_request(conn, request, "placed what was approved")
        html = webapp.render(conn).decode()
        self.assertIn("placed what was approved", html)
        self.assertIn("Place them in Sleeper now", html)

    def test_one_press_cannot_be_claimed_twice(self):
        # Two runs acting on one press would try to place the same claim
        # twice; the second finds nothing waiting.
        import store as st
        conn = self.ready()
        request = st.ask_to_submit(conn)
        st.claim_submit_request(conn, request)
        self.assertIsNone(st.pending_submit_request(conn))


class Stylesheet(unittest.TestCase):
    """The stylesheet is a Python string, which is a trap.

    A CSS escape like \\2212 is also an octal escape to Python: it becomes
    one control character and a stray digit, and the page draws a 2 where it
    meant a minus sign. Anything a designer would write as an escape has to
    be the character itself here.
    """

    def test_no_control_characters_reached_the_stylesheet(self):
        bad = [c for c in webapp.CSS if ord(c) < 32 and c not in "\n\t"]
        self.assertEqual(bad, [])


class ThursdayFirst(unittest.TestCase):
    """A deadline tonight and a deadline on Sunday are not one heading."""

    def build(self, *days):
        conn = st.connect(":memory:")
        rows = []
        for n, day in enumerate(days):
            rows.append({
                "league_id": "1", "league_name": "LEHG", "slot": "WR",
                "position": n, "verdict": "RED", "player_id": f"p{n}",
                "player_name": f"Player {n} (MIN WR)", "rank_text": "WR20",
                "better_name": f"Other {n} (GB WR)",
                "detail": "Analysts rank him higher.", "role": "starter",
                "pos": "WR", "matchup": "at CHI", "projection": 9.1,
                "overall_text": "120", "day": day, "locked": 0,
            })
        st.write_lineup_check(conn, "2026", 3, ["FantasyPros"], rows)
        return webapp.render_lineup(conn).decode().split(
            "</style></head>")[1]

    def test_a_thursday_call_gets_its_own_heading(self):
        body = self.build("Thu", "Sun")
        self.assertIn("Playing Thursday", body)
        self.assertIn("1 decision for Thursday night", body)

    def test_the_rest_stay_where_they_were(self):
        body = self.build("Thu", "Sun")
        self.assertIn("Fix these", body)

    def test_a_week_with_no_thursday_player_says_nothing(self):
        body = self.build("Sun", "Sun")
        self.assertNotIn("Playing Thursday", body)
        self.assertNotIn("Thursday night", body)

    def test_several_thursday_calls_are_counted(self):
        self.assertIn("2 decisions for Thursday night",
                      self.build("Thu", "Thu", "Sun"))

    def test_a_thursday_player_is_not_also_in_the_list_below(self):
        body = self.build("Thu")
        self.assertIn("Playing Thursday", body)
        # The only open call is the Thursday one, so nothing is left to fix.
        self.assertNotIn("Fix these", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
