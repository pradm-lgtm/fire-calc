#!/usr/bin/env python3
"""Tests for streaming a defense.

    python3 test_defense.py

All offline: projections and rankings are passed in, so what is under test
is the judgement rather than anyone's API.
"""

import unittest

import defense


PLAYERS = {
    "SF": {"position": "DEF", "full_name": "San Francisco 49ers"},
    "DAL": {"position": "DEF", "full_name": "Dallas Cowboys"},
    "NYJ": {"position": "DEF", "full_name": "New York Jets"},
    "CHI": {"position": "DEF", "full_name": "Chicago Bears"},
    "p1": {"position": "RB", "full_name": "Some Back"},
}
ROSTERS = [
    {"players": ["CHI", "p1"]},
    {"players": ["DAL"]},
]
MINE = {"players": ["CHI", "p1"]}
LEAGUE_WITH_DEF = {"roster_positions": ["QB", "RB", "WR", "TE", "DEF", "BN"]}
LEAGUE_WITHOUT = {"roster_positions": ["QB", "RB", "WR", "TE", "BN"]}


class WhichLeagues(unittest.TestCase):

    def test_a_league_with_a_defense_slot_wants_one(self):
        self.assertTrue(defense.wants_defense(LEAGUE_WITH_DEF))

    def test_a_league_without_one_is_left_alone(self):
        self.assertFalse(defense.wants_defense(LEAGUE_WITHOUT))

    def test_a_league_that_says_nothing_is_not_assumed_to_want_one(self):
        self.assertFalse(defense.wants_defense({}))


class WhoIsAvailable(unittest.TestCase):

    def test_defenses_nobody_holds(self):
        self.assertEqual(sorted(defense.available(ROSTERS, PLAYERS)),
                         ["NYJ", "SF"])

    def test_the_one_on_my_roster_is_found(self):
        self.assertEqual(defense.mine(MINE, PLAYERS), "CHI")

    def test_a_roster_with_no_defense_has_none(self):
        self.assertIsNone(defense.mine({"players": ["p1"]}, PLAYERS))

    def test_skill_players_are_not_defenses(self):
        self.assertEqual(defense.defenses(PLAYERS, ["p1"]), [])


class Choosing(unittest.TestCase):
    """This week decides; next week breaks ties."""

    def test_the_better_matchup_this_week_wins(self):
        rows = defense.candidates(["SF", "NYJ"],
                                  {"SF": 11.0, "NYJ": 6.0},
                                  {"SF": 5.0, "NYJ": 5.0})
        self.assertEqual(rows[0]["id"], "SF")

    def test_next_week_separates_two_that_stand_level(self):
        # Level on both signals; next week is the only thing left.
        rows = defense.candidates(["SF", "NYJ"],
                                  {"SF": 9.0, "NYJ": 9.0},
                                  {"SF": 12.0, "NYJ": 4.0})
        self.assertEqual(rows[0]["id"], "SF")

    def test_next_week_does_not_overturn_a_clearly_better_week(self):
        rows = defense.candidates(["SF", "NYJ"],
                                  {"SF": 4.0, "NYJ": 12.0},
                                  {"SF": 14.0, "NYJ": 2.0})
        self.assertEqual(rows[0]["id"], "NYJ")

    def test_the_analysts_can_change_the_order(self):
        # Second on projection, first with the analysts, better next week.
        free = ["SF", "NYJ", "DAL"]
        points = {"SF": 9.0, "NYJ": 8.5, "DAL": 8.0}
        ahead = {"SF": 3.0, "NYJ": 11.0, "DAL": 4.0}
        plain = defense.candidates(free, points, ahead)
        self.assertEqual(plain[0]["id"], "SF")
        with_analysts = defense.candidates(
            free, points, ahead,
            consensus={"NYJ": {"rank": 1}, "SF": {"rank": 9},
                       "DAL": {"rank": 12}})
        self.assertEqual(with_analysts[0]["id"], "NYJ")

    def test_a_defense_nobody_ranked_is_not_punished_for_it(self):
        # Only SF appears on the analysts' page; NYJ projects far better and
        # must not be pushed down for being absent from it.
        rows = defense.candidates(["SF", "NYJ"], {"SF": 4.0, "NYJ": 14.0}, {},
                                  consensus={"SF": {"rank": 1}})
        self.assertEqual(rows[0]["id"], "NYJ")

    def test_the_rank_is_still_carried_for_the_card(self):
        rows = defense.candidates(["SF"], {"SF": 8.0}, {},
                                  consensus={"SF": {"rank": 3}})
        self.assertEqual(rows[0]["rank"], 3)

    def test_two_signals_that_agree_leave_the_order_alone(self):
        rows = defense.candidates(["SF", "NYJ"], {"SF": 12.0, "NYJ": 5.0}, {},
                                  consensus={"SF": {"rank": 2},
                                             "NYJ": {"rank": 20}})
        self.assertEqual([r["id"] for r in rows], ["SF", "NYJ"])


class Suggesting(unittest.TestCase):

    def suggest(self, this_week, next_week=None, league=LEAGUE_WITH_DEF,
                **kw):
        return defense.suggest(league, MINE, PLAYERS, ROSTERS, this_week,
                               next_week or {}, **kw)

    def test_a_clear_upgrade_is_proposed(self):
        got = self.suggest({"SF": 12.0, "NYJ": 4.0, "CHI": 5.0})
        self.assertIsNotNone(got)
        add, drop, _why = got
        self.assertEqual(add, "SF")
        self.assertEqual(drop, "CHI")

    def test_the_drop_is_the_defense_being_streamed(self):
        _add, drop, _why = self.suggest({"SF": 12.0, "CHI": 5.0})
        self.assertEqual(drop, "CHI")

    def test_a_marginal_gain_is_not_worth_a_claim(self):
        self.assertIsNone(self.suggest({"SF": 9.5, "CHI": 9.0}))

    def test_a_league_with_no_defense_slot_is_never_told_to_stream(self):
        self.assertIsNone(self.suggest({"SF": 20.0, "CHI": 1.0},
                                       league=LEAGUE_WITHOUT))

    def test_a_roster_with_no_defense_takes_the_best_available(self):
        got = defense.suggest(LEAGUE_WITH_DEF, {"players": ["p1"]}, PLAYERS,
                              ROSTERS, {"SF": 9.0, "NYJ": 3.0}, {})
        add, drop, _why = got
        self.assertEqual(add, "SF")
        self.assertIsNone(drop)

    def test_nothing_projected_at_all_proposes_nothing(self):
        self.assertIsNone(defense.suggest(
            LEAGUE_WITH_DEF, {"players": ["p1"]}, PLAYERS, ROSTERS, {}, {}))

    def test_a_league_where_every_defense_is_held_proposes_nothing(self):
        rosters = [{"players": ["CHI", "SF", "NYJ", "DAL"]}]
        self.assertIsNone(defense.suggest(
            LEAGUE_WITH_DEF, MINE, PLAYERS, rosters, {"SF": 20.0}, {}))


class TheReason(unittest.TestCase):
    """The card has to be checkable, not just confident."""

    def why(self, **kw):
        best = {"id": "SF", "points": 12.0, "next_points": 9.0, "rank": 4}
        holding = {"id": "CHI", "points": 5.0, "next_points": 6.0}
        return defense.why(best, holding, **kw)

    def test_it_names_both_numbers(self):
        said = self.why()
        self.assertIn("projected 12.0 this week", said)
        self.assertIn("5.0", said)

    def test_it_says_the_size_of_the_gain(self):
        self.assertIn("+7.0", self.why())

    def test_next_week_is_mentioned_with_the_opponent(self):
        # The matchup carries its own preposition.
        said = self.why(next_opponent="at CAR")
        self.assertIn("9.0 next week at CAR", said)
        self.assertNotIn("against at", said)

    def test_the_analyst_rank_appears(self):
        self.assertIn("DST4", self.why())

    def test_a_first_defense_does_not_compare_against_nothing(self):
        said = defense.why({"id": "SF", "points": 12.0, "next_points": 0.0,
                            "rank": None}, None)
        self.assertIn("12.0", said)
        self.assertNotIn("+", said)


class InAWeeklyRun(unittest.TestCase):
    """The defense reaches a proposal, and only where it should."""

    PLAYERS = {
        "CHI": {"full_name": "Chicago Bears", "position": "DEF",
                "team": "CHI"},
        "SF": {"full_name": "San Francisco 49ers", "position": "DEF",
               "team": "SF"},
        "rb1": {"full_name": "Some Back", "position": "RB", "team": "GB"},
    }
    ROSTER = {"roster_id": 1, "owner_id": "me", "starters": ["rb1", "CHI"],
              "players": ["rb1", "CHI"], "settings": {"waiver_budget_used": 0}}
    LEAGUE = {"league_id": "L1", "name": "OTG Alumni",
              "settings": {"num_teams": 10, "waiver_budget": 100},
              "scoring_settings": {"rec": 0.5},
              "roster_positions": ["RB", "DEF", "BN"]}
    WEEKS = {"points": {"SF": 13.0, "CHI": 5.5},
             "next_points": {"SF": 8.0}, "next_games": {"SF": "at CAR"},
             "dst_ranks": {}}

    def run_it(self, league=None, weeks=None):
        import run_weekly as rw
        import sleeper_client as sc
        real = sc.league_rosters
        sc.league_rosters = lambda _lid: [self.ROSTER]
        try:
            return rw.proposals_for_league(
                league or self.LEAGUE, "me", self.PLAYERS, {}, {}, 3,
                week=2, weeks=weeks or self.WEEKS)
        finally:
            sc.league_rosters = real

    def test_it_survives_a_week_the_articles_produced_nothing(self):
        # Nobody writes up a defense as a player worth owning, so this must
        # not sit behind "no article recommended anyone".
        rows, why = self.run_it()
        self.assertEqual(len(rows), 1, why)
        self.assertEqual(rows[0]["add_position"], "DEF")

    def test_it_drops_the_defense_being_streamed(self):
        rows, _why = self.run_it()
        self.assertEqual(rows[0]["drop_player_id"], "CHI")

    def test_the_bid_stays_small(self):
        rows, _why = self.run_it()
        self.assertLessEqual(rows[0]["bid"], 2)

    def test_a_league_with_no_defense_slot_gets_none(self):
        league = dict(self.LEAGUE, roster_positions=["RB", "BN"])
        rows, _why = self.run_it(league=league)
        self.assertEqual(rows, [])

    def test_no_swap_worth_making_produces_nothing(self):
        weeks = {"points": {"SF": 5.0, "CHI": 9.0}, "next_points": {},
                 "next_games": {}}
        rows, _why = self.run_it(weeks=weeks)
        self.assertEqual(rows, [])


class RanksAreActuallyFetched(unittest.TestCase):
    """The half that was missing: nothing was asking for the DST page.

    dst_ranks was initialised empty and never filled, so the analyst signal
    was not merely unused in the ordering - it never arrived, and the line
    about it on the card could not have printed.
    """

    def test_the_outlook_asks_for_the_defense_rankings(self):
        import run_weekly as rw
        asked = {}

        def fake(players, verbose=False):
            asked["players"] = players
            return {"SF": {"rank": 2}}
        real = rw.defense_ranks
        rw.defense_ranks = fake
        try:
            out = rw.week_outlook("2026", 2, players={"SF": {}})
        finally:
            rw.defense_ranks = real
        self.assertEqual(out["dst_ranks"], {"SF": {"rank": 2}})
        self.assertEqual(asked["players"], {"SF": {}})

    def test_it_only_wants_the_defense_source(self):
        import rankings as rk
        wanted = [e for e in rk.load_sources()
                  if "DEF" in [str(p).upper()
                               for p in (e.get("positions") or [])]]
        self.assertEqual(len(wanted), 1)
        self.assertIn("dst", wanted[0]["url"].lower())

    def test_a_page_that_will_not_load_is_not_fatal(self):
        import run_weekly as rw
        import lineup
        real = lineup.gather_rankings
        lineup.gather_rankings = lambda *_a, **_k: (_ for _ in ()).throw(
            RuntimeError("no browser"))
        try:
            self.assertEqual(rw.defense_ranks({"SF": {}}), {})
        finally:
            lineup.gather_rankings = real


if __name__ == "__main__":
    unittest.main(verbosity=2)
