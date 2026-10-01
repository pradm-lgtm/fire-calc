#!/usr/bin/env python3
"""Tests for the start/sit verdicts and the row shape they travel in.

    python3 test_lineup.py

The row shape is worth a test because it crosses three boundaries - the
terminal report, an HTTP post, and an INSERT - and a renamed key would only
surface on a Sunday morning.
"""

import unittest

import lineup
import rankings as rk
import store as st

PLAYERS = {
    "qb1": {"position": "QB", "full_name": "Dak Prescott", "team": "DAL",
            "fantasy_positions": ["QB"]},
    "qb2": {"position": "QB", "full_name": "Jayden Daniels", "team": "WAS",
            "fantasy_positions": ["QB"]},
    "rb1": {"position": "RB", "full_name": "J.K. Dobbins", "team": "DEN",
            "fantasy_positions": ["RB"]},
    "rb2": {"position": "RB", "full_name": "MarShawn Lloyd", "team": "GB",
            "fantasy_positions": ["RB"]},
}

LEAGUE = {"league_id": "77", "name": "OTG Alumni",
          "roster_positions": ["QB", "FLEX", "BN", "BN"]}

ROSTER = {"starters": ["qb1", "rb1"],
          "players": ["qb1", "rb1", "qb2", "rb2"]}


def consensus(qb1, qb2, rb1, rb2):
    return rk.merge({"page": {
        "QB": {"qb1": qb1, "qb2": qb2},
        rk.OVERALL: {"rb1": rb1, "rb2": rb2},
    }})


class Verdicts(unittest.TestCase):

    def rows(self, **kw):
        return lineup.flag_rows(LEAGUE, ROSTER, PLAYERS, consensus(**kw),
                                lineup.EMPTY_WEEK)

    def test_a_small_gap_is_green(self):
        rows = self.rows(qb1=10, qb2=9, rb1=60, rb2=59)
        self.assertEqual([r["verdict"] for r in rows], ["GREEN", "GREEN"])

    def test_a_wide_gap_is_red_and_names_the_alternative(self):
        rows = self.rows(qb1=40, qb2=2, rb1=60, rb2=59)
        self.assertEqual(rows[0]["verdict"], "RED")
        self.assertEqual(rows[0]["better_name"], "Jayden Daniels (WAS QB)")
        self.assertEqual(rows[0]["detail"],
                         "Analysts rank Daniels 38 spots higher.")

    def test_a_starter_nobody_ranks_is_not_called_fine(self):
        # Counting an unjudgeable starter as agreement claims a check that
        # never happened.
        rows = lineup.flag_rows(LEAGUE, ROSTER, PLAYERS,
                                rk.merge({"page": {"QB": {"qb1": 1, "qb2": 2}}}),
                                lineup.EMPTY_WEEK)
        self.assertEqual(rows[1]["verdict"], "UNKNOWN")

    def test_flex_is_judged_on_the_overall_order(self):
        rows = self.rows(qb1=10, qb2=9, rb1=66, rb2=20)
        self.assertEqual(rows[1]["verdict"], "RED")
        self.assertEqual(rows[1]["slot"], "FLEX")


class Wording(unittest.TestCase):
    """One reason, in a sentence, in the active voice."""

    def rows(self, **kw):
        return lineup.flag_rows(LEAGUE, ROSTER, PLAYERS, consensus(**kw),
                                lineup.EMPTY_WEEK)

    def test_an_injury_is_the_whole_reason(self):
        # Telling someone their out player also ranks a spot low is a second,
        # weaker argument for something already settled.
        hurt = dict(PLAYERS)
        hurt["qb1"] = dict(PLAYERS["qb1"], injury_status="Out")
        rows = lineup.flag_rows(LEAGUE, ROSTER, hurt,
                                consensus(qb1=10, qb2=11, rb1=60, rb2=59),
                                lineup.EMPTY_WEEK)
        self.assertEqual(rows[0]["detail"],
                         "Prescott is out this week. Daniels is your best "
                         "replacement.")
        self.assertNotIn("rank", rows[0]["detail"])

    def test_small_counts_read_as_words(self):
        # "1 places better" was the wording that prompted this.
        self.assertEqual(lineup.spots(1), "a spot")
        self.assertEqual(lineup.spots(3), "three spots")
        self.assertEqual(lineup.spots(11), "11 spots")

    def test_a_wide_gap_counts_the_spots(self):
        rows = self.rows(qb1=40, qb2=2, rb1=60, rb2=59)
        self.assertIn("38 spots higher", rows[0]["detail"])

    def test_a_green_slot_says_nothing(self):
        rows = self.rows(qb1=10, qb2=9, rb1=60, rb2=59)
        self.assertEqual(rows[0]["detail"], "")


class RowShape(unittest.TestCase):

    def test_every_row_is_accepted_by_the_database(self):
        rows = lineup.flag_rows(LEAGUE, ROSTER, PLAYERS,
                                consensus(40, 2, 66, 20), lineup.EMPTY_WEEK)
        conn = st.connect(":memory:")
        check_id = st.start_lineup_check(conn, "2026", 1, ["page"])
        for row in rows:
            st.add_lineup_flag(conn, check_id, **row)
        stored = st.lineup_flags(conn, check_id)
        self.assertEqual(len(stored), len(rows))
        self.assertEqual(stored[0]["league_name"], "OTG Alumni")
        self.assertEqual([r["slot"] for r in stored], ["QB", "FLEX"])

    def test_rows_survive_a_json_round_trip(self):
        import json
        rows = lineup.flag_rows(LEAGUE, ROSTER, PLAYERS,
                                consensus(40, 2, 66, 20), lineup.EMPTY_WEEK)
        self.assertEqual(json.loads(json.dumps(rows)), rows)


class WeekContext(unittest.TestCase):
    """Opponents and points, read off records that may not carry either."""

    import nfl_week as nw

    def test_home_and_away_read_the_right_way_round(self):
        got = self.nw.matchups_from([
            {"team": "GB", "opponent": "CHI", "home": False},
            {"team": "DAL", "opponent": "PHI", "home": True}])
        self.assertEqual(got, {"GB": "at CHI", "DAL": "vs PHI"})

    def test_records_without_an_opponent_are_skipped(self):
        self.assertEqual(self.nw.matchups_from([{"team": "WAS"}]), {})

    def test_the_two_spellings_of_washington_are_reconciled(self):
        # Sleeper says WAS and ESPN says WSH; every player record in this
        # project comes from Sleeper, so Sleeper's spelling has to win or
        # the opponent never matches a roster.
        got = self.nw.matchups_from([{"team": "PHI", "opponent": "WSH",
                                      "home": True}])
        self.assertEqual(got, {"PHI": "vs WAS"})

    def test_points_prefer_half_ppr(self):
        got = self.nw.points_from([{"player_id": "1", "stats": {
            "pts_ppr": 20.0, "pts_half_ppr": 17.5, "pts_std": 15.0}}])
        self.assertEqual(got, {"1": 17.5})

    def test_a_record_with_no_points_at_all_is_skipped(self):
        self.assertEqual(self.nw.points_from([{"player_id": "1", "stats": {}}]),
                         {})


class Kickoffs(unittest.TestCase):
    """A day is not a kickoff."""

    import nfl_week as nw

    def test_a_bare_date_is_not_a_time(self):
        # It parses happily to midnight, and midnight UTC on game day is the
        # evening before in America, so every Sunday game read as started
        # from Saturday night onward.
        self.assertIsNone(self.nw._epoch("2026-09-14"))

    def test_a_real_timestamp_is_kept(self):
        self.assertEqual(self.nw._epoch("2026-09-14T17:00:00Z"),
                         self.nw._epoch("2026-09-14T17:00:00+00:00"))

    def test_milliseconds_are_recognised(self):
        self.assertEqual(self.nw._epoch(1789416900000),
                         self.nw._epoch(1789416900))

    def test_an_unknown_direction_names_the_opponent_without_guessing(self):
        # Asserting "at" for every game because no field said otherwise is
        # worse than saying only who they play.
        self.assertEqual(self.nw.matchups_from([{"team": "JAX",
                                                 "opponent": "CLE"}]),
                         {"JAX": "CLE"})

    def test_a_known_direction_is_used(self):
        self.assertEqual(self.nw.matchups_from([{"team": "JAX",
                                                 "opponent": "CLE",
                                                 "home": True}]),
                         {"JAX": "vs CLE"})


class WhichDay(unittest.TestCase):
    """The day travels with the player, so the page can sort by deadline."""

    def week(self, kickoff):
        return {"points": {}, "games": {"MIN": "at CHI"},
                "kickoffs": {"MIN": kickoff}, "statuses": {}}

    def test_a_thursday_player_is_marked_thursday(self):
        from datetime import datetime, timezone
        thursday = datetime(2026, 9, 18, 0, 15,
                            tzinfo=timezone.utc).timestamp()
        got = lineup.context_for({"team": "MIN", "position": "WR"}, "p1",
                                 self.week(thursday))
        self.assertEqual(got["day"], "Thu")

    def test_a_sunday_player_is_not(self):
        from datetime import datetime, timezone
        sunday = datetime(2026, 9, 20, 17, 0, tzinfo=timezone.utc).timestamp()
        got = lineup.context_for({"team": "MIN", "position": "WR"}, "p1",
                                 self.week(sunday))
        self.assertEqual(got["day"], "Sun")

    def test_an_unknown_kickoff_leaves_the_day_unset(self):
        got = lineup.context_for({"team": "MIN", "position": "WR"}, "p1",
                                 self.week(None))
        self.assertIsNone(got["day"])

    def test_a_week_with_no_kickoffs_at_all_does_not_explode(self):
        got = lineup.context_for({"team": "MIN", "position": "WR"}, "p1",
                                 lineup.EMPTY_WEEK)
        self.assertIsNone(got["day"])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TheYahooLineups(unittest.TestCase):
    """The start/sit model never cared which site a roster came from.

    It reads a league's slots, who is in them, and what the analysts
    think. All Yahoo needed was the roster in the shape the Sleeper one
    arrives in - which yahoo_waivers already builds for the waiver side -
    so this is a loop rather than a second model.
    """

    import yahoo_waivers as yw

    LEAGUE = yw.as_league(
        {"key": "470.l.715420", "name": "The Minor League", "teams": 10},
        ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN"])
    PLAYERS = {
        "q": {"full_name": "My QB", "position": "QB", "team": "JAX",
              "fantasy_positions": ["QB"]},
        "r1": {"full_name": "Back One", "position": "RB", "team": "BUF",
               "fantasy_positions": ["RB"]},
        "r2": {"full_name": "Hurt Back", "position": "RB", "team": "GB",
               "injury_status": "Out", "fantasy_positions": ["RB"]},
        "w1": {"full_name": "Wide One", "position": "WR", "team": "BAL",
               "fantasy_positions": ["WR"]},
        "b1": {"full_name": "Bench Man", "position": "RB", "team": "MIA",
               "fantasy_positions": ["RB"]},
    }
    MINE = {"players": ["q", "r1", "r2", "w1", "b1"],
            "starters": ["q", "r1", "r2", "w1"], "reserve": []}

    def flags(self):
        return lineup.flag_rows(self.LEAGUE, self.MINE, self.PLAYERS, {},
                                lineup.EMPTY_WEEK)

    def test_every_started_player_gets_a_row(self):
        self.assertEqual(len(self.flags()), 4)

    def test_a_started_player_who_is_out_is_flagged(self):
        hurt = [r for r in self.flags() if r["player_id"] == "r2"]
        self.assertEqual(hurt[0]["verdict"], "RED")

    def test_the_rows_name_the_yahoo_league(self):
        """So the page can group them, as it does for the Sleeper ones."""
        for row in self.flags():
            self.assertEqual(row["league_name"], "The Minor League")
            self.assertEqual(row["league_id"], "470.l.715420")

    def test_the_bench_is_read_too(self):
        bench = lineup.bench_rows(self.LEAGUE, self.MINE, self.PLAYERS, {},
                                  lineup.EMPTY_WEEK)
        self.assertEqual([b["player_id"] for b in bench], ["b1"])

    def test_a_man_on_yahoo_reserve_is_not_a_starter(self):
        """Yahoo says IR outright, where Sleeper needs a reserve list."""
        squad = [{"key": "470.p.1", "slot": "RB"},
                 {"key": "470.p.2", "slot": "IR"}]
        got = self.yw.as_roster(squad, {"470.p.1": "r1", "470.p.2": "r2"})
        self.assertEqual(got["starters"], ["r1"])
        self.assertEqual(got["reserve"], ["r2"])

    def test_no_yahoo_configured_means_no_rows_and_no_error(self):
        import yahoo_client as yc
        real = yc.configured
        yc.configured = lambda: False
        try:
            self.assertEqual(lineup.yahoo_rows({}, {}, lineup.EMPTY_WEEK), [])
        finally:
            yc.configured = real

    def test_a_yahoo_failure_does_not_take_the_sleeper_check_with_it(self):
        import yahoo_client as yc
        import yahoo_waivers as yw
        real_conf, real_gather = yc.configured, yw.gather
        yc.configured = lambda: True

        def broken(*a, **k):
            raise RuntimeError("token expired")
        yw.gather = broken
        try:
            self.assertEqual(lineup.yahoo_rows({}, {}, lineup.EMPTY_WEEK), [])
        finally:
            yc.configured, yw.gather = real_conf, real_gather

    def test_the_check_asks_for_them(self):
        source = open("lineup.py").read()
        self.assertIn("found.extend(yahoo_rows(", source)
