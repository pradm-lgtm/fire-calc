#!/usr/bin/env python3
"""The Yahoo leagues, decided by the model the Sleeper leagues use.

The value model took a week of being wrong in public to get right. None
of that work is about Sleeper - it is merely keyed by Sleeper ids, which
is a different thing. So this translates rather than duplicating, and the
test that matters most is that there is one decision path, not two.
"""

import unittest

import expert_waivers as ew
import run_weekly as rw
import yahoo_waivers as yw


class TurningAYahooRosterIntoOneTheModelReads(unittest.TestCase):

    SQUAD = [{"key": "470.p.1", "slot": "QB"},
             {"key": "470.p.2", "slot": "BN"},
             {"key": "470.p.3", "slot": "IR"},
             {"key": "470.p.4", "slot": "W/R/T"},
             {"key": "470.p.9", "slot": "BN"}]
    MAP = {"470.p.1": "s1", "470.p.2": "s2", "470.p.3": "s3",
           "470.p.4": "s4"}

    SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "W/R/T", "K", "DEF",
             "BN", "BN", "IR"]

    def roster(self):
        return yw.as_roster(self.SQUAD, self.MAP, self.SLOTS)

    def test_everybody_bridged_is_on_the_roster(self):
        self.assertEqual(self.roster()["players"],
                         ["s1", "s2", "s3", "s4"])

    def test_a_filled_slot_means_a_starter(self):
        got = [p for p in self.roster()["starters"] if p != "0"]
        self.assertEqual(sorted(got), ["s1", "s4"])

    def test_the_starters_are_in_the_leagues_slot_order(self):
        """The start/sit check takes starters[i] to be the man in
        slots[i]. Built in the order Yahoo returned the squad, a running
        back was judged as a quarterback."""
        got = self.roster()["starters"]
        self.assertEqual(got[0], "s1")                 # QB slot
        self.assertEqual(got[self.SLOTS.index("W/R/T")], "s4")

    def test_an_empty_slot_keeps_its_place(self):
        """Spelled "0", the way Sleeper spells one, so the places after
        it still mean what they say."""
        self.assertIn("0", self.roster()["starters"])

    def test_every_kind_of_yahoo_reserve_is_reserve(self):
        """IR, IR+ and IL are all "not in your lineup". Only a bare IR
        was handled, so the others became starters and the page told him
        to sit men already on his injured list."""
        squad = [{"key": "a", "slot": "IR"}, {"key": "b", "slot": "IR+"},
                 {"key": "c", "slot": "IL"}]
        got = yw.as_roster(squad, {"a": "x", "b": "y", "c": "z"},
                           self.SLOTS)
        self.assertEqual(sorted(got["reserve"]), ["x", "y", "z"])
        self.assertEqual([p for p in got["starters"] if p != "0"], [])

    def test_a_slot_we_do_not_know_becomes_a_bench_player(self):
        """Which is the harmless way to be wrong about one."""
        squad = [{"key": "a", "slot": "SOMETHING_NEW"}]
        got = yw.as_roster(squad, {"a": "x"}, self.SLOTS)
        self.assertNotIn("x", got["starters"])
        self.assertNotIn("x", got["reserve"])
        self.assertIn("x", got["players"])

    def test_without_slots_it_guesses_rather_than_emptying_the_lineup(self):
        squad = [{"key": "a", "slot": "RB"}, {"key": "b", "slot": "BN"}]
        got = yw.as_roster(squad, {"a": "x", "b": "y"})
        self.assertEqual(got["starters"], ["x"])

    def test_the_flex_counts_as_starting(self):
        """Yahoo calls it W/R/T and it is a man in your lineup."""
        self.assertIn("s4", self.roster()["starters"])

    def test_the_bench_does_not(self):
        self.assertNotIn("s2", self.roster()["starters"])

    def test_injured_reserve_is_its_own_list(self):
        """Which is what keeps him off the drop list entirely."""
        self.assertEqual(self.roster()["reserve"], ["s3"])
        self.assertNotIn("s3", self.roster()["starters"])

    def test_somebody_who_did_not_bridge_is_left_out(self):
        """Better a roster short one man than one holding a wrong player."""
        self.assertNotIn("470.p.9", self.roster()["players"])

    def test_an_empty_roster_is_not_an_error(self):
        self.assertEqual(yw.as_roster([], {})["players"], [])


class TurningAYahooLeagueIntoOneTheModelReads(unittest.TestCase):

    LEAGUE = {"key": "470.l.715420", "name": "The Minor League", "teams": 10}
    SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "DEF",
             "BN", "BN", "BN", "IR"]

    def built(self):
        return yw.as_league(self.LEAGUE, self.SLOTS)

    def test_the_slots_come_through_as_roster_positions(self):
        self.assertEqual(self.built()["roster_positions"], self.SLOTS)

    def test_positional_depth_can_read_it(self):
        """The whole point: this is now a league the model understands."""
        players = {"a": {"position": "QB"}, "b": {"position": "RB"}}
        roster = {"players": ["a", "b"]}
        depth = ew.positional_depth(self.built(), roster, players)
        self.assertIn("QB", depth)
        self.assertEqual(depth["QB"][1], 1)

    def test_the_team_count_reaches_the_droppability_line(self):
        self.assertEqual(ew.replaceable_after(self.built()), 10 * 8)

    def test_there_is_no_budget(self):
        """Waiver priority. No money, so no bid and no budget line."""
        self.assertEqual(self.built()["settings"]["waiver_budget"], 0)

    def test_a_league_with_no_team_count_still_builds(self):
        got = yw.as_league({"key": "470.l.1", "name": "x"}, ["QB"])
        self.assertEqual(got["settings"]["num_teams"], 10)


class OneDecisionPathNotTwo(unittest.TestCase):
    """A fault found in one league should be fixed in four."""

    LEAGUE = {"league_id": "470.l.715420", "name": "The Minor League",
              "roster_positions": ["QB", "RB", "RB", "WR", "WR", "TE",
                                   "FLEX", "DEF", "BN", "BN"],
              "settings": {"num_teams": 10, "waiver_budget": 0}}
    PLAYERS = {
        "q": {"full_name": "My QB", "position": "QB", "search_rank": 40},
        "r1": {"full_name": "Back One", "position": "RB",
               "search_rank": 30},
        "r2": {"full_name": "Back Two", "position": "RB",
               "search_rank": 60},
        "junk": {"full_name": "Bench Junk", "position": "WR",
                 "search_rank": 900},
        "good": {"full_name": "Free Agent", "position": "WR",
                 "search_rank": 25, "depth_chart_order": 1},
    }
    MINE = {"players": ["q", "r1", "r2", "junk"],
            "starters": ["q", "r1", "r2"], "reserve": []}
    CONSENSUS = {"good": {"count": 3, "sources": ["x"],
                          "contexts": [("x", "Add him.")],
                          "faab_median": None, "faab_values": []}}

    def run_it(self, **kw):
        args = dict(league=self.LEAGUE, mine=self.MINE, players=self.PLAYERS,
                    available=["good"], consensus=self.CONSENSUS,
                    trending={}, weeks={}, week=4, moves=8, remaining=0,
                    rosters=[], cost={}, advice={}, byes={},
                    platform="yahoo", note="10-team, half-PPR")
        args.update(kw)
        return rw.decide(**args)

    def test_a_yahoo_league_produces_proposals(self):
        rows, why, _skipped = self.run_it()
        self.assertTrue(rows, why)

    def test_they_are_tagged_as_yahoo(self):
        rows, _why, _skipped = self.run_it()
        self.assertEqual(rows[0]["platform"], "yahoo")

    def test_there_is_no_bid_on_any_of_them(self):
        """Waiver priority: what a claim costs is your place in the queue."""
        rows, _why, _skipped = self.run_it()
        for row in rows:
            self.assertIsNone(row["bid"])
            self.assertIsNone(row["bid_low"])
            self.assertIsNone(row["bid_high"])

    def test_the_league_note_is_carried_rather_than_computed(self):
        """league_note reads Sleeper settings, which Yahoo does not have."""
        rows, _why, _skipped = self.run_it()
        self.assertEqual(rows[0]["league_note"], "10-team, half-PPR")

    def test_the_same_gates_apply(self):
        """A third quarterback is refused in Yahoo too, by the same rule."""
        players = dict(self.PLAYERS)
        players["qb2"] = {"full_name": "Backup", "position": "QB",
                          "search_rank": 200}
        players["freeqb"] = {"full_name": "A Third QB", "position": "QB",
                             "search_rank": 55}
        mine = dict(self.MINE, players=self.MINE["players"] + ["qb2"])
        skipped = []
        self.run_it(players=players, mine=mine, available=["freeqb"],
                    consensus={"freeqb": self.CONSENSUS["good"]},
                    skipped=skipped)
        self.assertTrue(any("could never play" in why
                            for _who, _pos, why in skipped), skipped)

    def test_sleeper_still_gets_its_own_platform(self):
        rows, _why, _skipped = self.run_it(platform="sleeper")
        self.assertEqual(rows[0]["platform"], "sleeper")


if __name__ == "__main__":
    unittest.main(verbosity=2)
