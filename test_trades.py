#!/usr/bin/env python3
"""Tests for finding trades both sides would take.

    python3 test_trades.py

A trade happens when both managers think they got better, so the thing worth
testing is that a package only surfaces when both starting lineups improve -
and that nothing surfaces just because the values happen to balance.
"""

import unittest

import trade_values as tv
import trades

# A league where you start one of each and a flex.
LEAGUE = {"league_id": "1", "name": "Test",
          "roster_positions": ["QB", "RB", "WR", "FLEX", "BN", "BN"]}

PLAYERS = {
    "qb1": {"position": "QB"}, "qb2": {"position": "QB"},
    "rb1": {"position": "RB"}, "rb2": {"position": "RB"},
    "rb3": {"position": "RB"}, "rb4": {"position": "RB"},
    "wr1": {"position": "WR"}, "wr2": {"position": "WR"},
    "wr3": {"position": "WR"}, "wr4": {"position": "WR"},
}


def values(**overrides):
    base = {"qb1": 5000, "qb2": 1000,
            "rb1": 9000, "rb2": 8000, "rb3": 7000, "rb4": 500,
            "wr1": 9000, "wr2": 800, "wr3": 700, "wr4": 600}
    base.update(overrides)
    return {pid: {"value": float(v), "position": PLAYERS[pid]["position"],
                  "name": pid, "rank": None, "pos_rank": None, "trend": None}
            for pid, v in base.items()}


class Lineups(unittest.TestCase):

    SLOTS = trades.starting_slots(LEAGUE)

    def test_the_best_legal_lineup_is_valued_not_the_whole_roster(self):
        ids = ["qb1", "rb1", "rb2", "rb3", "wr1"]
        # QB 5000 + RB 9000 + WR 9000 + flex 8000. rb3 does not start.
        self.assertEqual(
            trades.lineup_value(ids, PLAYERS, values(), self.SLOTS), 31000)

    def test_a_tight_slot_is_filled_before_a_loose_one(self):
        # Letting flex pick first would take the best running back and
        # strand the running back slot with the scraps.
        ids = ["qb1", "rb1", "wr1"]
        self.assertEqual(
            trades.lineup_value(ids, PLAYERS, values(), self.SLOTS), 23000)

    def test_a_player_nobody_prices_counts_as_nothing(self):
        self.assertEqual(
            trades.lineup_value(["unknown"], PLAYERS, values(), self.SLOTS), 0)


class Offers(unittest.TestCase):
    """You are deep at running back and thin at receiver; they are the
    reverse. That is the trade that should surface."""

    MINE = {"roster_id": 1,
            "players": ["qb1", "rb1", "rb2", "rb3", "wr2", "wr3"]}
    THEIRS = {"roster_id": 2,
              "players": ["qb2", "wr1", "wr4", "rb4"]}

    def find(self, **kw):
        return trades.offers(LEAGUE, self.MINE, self.THEIRS, PLAYERS,
                             values(**kw))

    def test_a_package_that_helps_both_sides_is_found(self):
        found = self.find()
        self.assertTrue(found)
        for offer in found:
            self.assertGreater(offer["my_gain"], 0)
            self.assertGreater(offer["their_gain"], 0)

    def test_the_surplus_goes_out_and_the_need_comes_in(self):
        best = self.find()[0]
        self.assertEqual(best["get"], ["wr1"])
        # A spare running back leaves. What goes with it is whatever makes
        # the values meet, which need not be another running back.
        self.assertTrue(any(p.startswith("rb") for p in best["give"]))

    def test_trading_the_best_player_at_a_deep_position_is_allowed(self):
        # Three good running backs and one starting spot means the best of
        # them is worth more to somebody else than as your flex.
        best = self.find()[0]
        self.assertIn("rb1", best["give"])
        self.assertGreater(best["my_gain"], 0)

    def test_a_package_only_one_side_gains_from_is_not_offered(self):
        # Their roster gets nothing it can start, so no version of this is
        # an offer however the values land.
        theirs = {"roster_id": 2, "players": ["wr1", "wr4", "qb2", "rb1"]}
        for offer in trades.offers(LEAGUE, self.MINE, theirs, PLAYERS,
                                   values()):
            self.assertGreater(offer["their_gain"], 0)

    def test_a_lopsided_package_is_dropped(self):
        # Fair by lineup gain is not enough; nobody looks at a robbery.
        for offer in self.find(wr1=40000):
            self.assertLessEqual(abs(offer["tilt"]), 25)

    def test_protected_players_are_never_offered(self):
        found = trades.offers(LEAGUE, self.MINE, self.THEIRS, PLAYERS,
                              values(), protect={"rb1", "rb2"})
        for offer in found:
            self.assertNotIn("rb1", offer["give"])
            self.assertNotIn("rb2", offer["give"])


class NeverDrop(unittest.TestCase):
    """The list of players you will not cut applies to trades too."""

    NAMED = {"rb1": {"position": "RB", "full_name": "Keep Him"},
             "rb2": {"position": "RB", "full_name": "Spare One"},
             "rb3": {"position": "RB", "full_name": "Spare Two"},
             "wr1": {"position": "WR", "full_name": "Want Him"},
             "wr2": {"position": "WR", "full_name": "Filler"},
             "wr3": {"position": "WR", "full_name": "Filler Two"},
             "wr4": {"position": "WR", "full_name": "Their Spare"},
             "qb1": {"position": "QB", "full_name": "My QB"},
             "qb2": {"position": "QB", "full_name": "Their QB"},
             "rb4": {"position": "RB", "full_name": "Their Back"}}

    def test_a_protected_player_is_never_in_a_package(self):
        # A waiver drop costs a roster spot; a trade hands him to a rival.
        found = trades.offers(LEAGUE, Offers.MINE, Offers.THEIRS, self.NAMED,
                              values(), protect={"keep him"})
        self.assertTrue(found)
        for offer in found:
            self.assertNotIn("rb1", offer["give"])


class RosterSpots(unittest.TestCase):
    """Sending two for one hands the other side a roster spot."""

    VALUES = values()

    def test_the_spare_spot_counts_toward_their_side(self):
        plain = trades.fairness(["rb2"], ["wr1"], self.VALUES, 0, 0)
        with_spot = trades.fairness(["rb2", "rb3"], ["wr1"], self.VALUES, 1, 500)
        self.assertEqual(with_spot[1] - plain[1], 500)

    def test_replacement_value_ignores_rostered_players(self):
        free = tv.replacement_value(self.VALUES, "RB", {"rb1", "rb2", "rb3"})
        self.assertEqual(free, 500)      # rb4, the only running back left

    def test_no_free_agent_at_all_is_worth_nothing(self):
        self.assertEqual(tv.replacement_value(self.VALUES, "K", set()), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
