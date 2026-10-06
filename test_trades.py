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
        # "Keep Him" is rb1, and every package the unprotected run finds
        # sends him. Protecting him may well leave no trade at all, which
        # is the correct answer, so what matters is that he is not in one.
        loose = trades.offers(LEAGUE, Offers.MINE, Offers.THEIRS, self.NAMED,
                              values())
        self.assertTrue(any("rb1" in o["give"] for o in loose))

        held = trades.offers(LEAGUE, Offers.MINE, Offers.THEIRS, self.NAMED,
                             values(), protect={"keep him"})
        for offer in held:
            self.assertNotIn("rb1", offer["give"])


class EmptiedSlots(unittest.TestCase):
    """A slot you empty is not a slot you leave empty."""

    SLOTS = trades.starting_slots(LEAGUE)
    # A quarterback nobody has rostered, worth half of the one you start.
    FREE = {"QB": 2500.0}

    def test_a_gap_is_filled_from_the_wire_not_counted_as_zero(self):
        # Trading your only quarterback costs the difference between him and
        # whoever is available, not his whole value, and that difference is
        # often worth paying.
        whole = trades.lineup_value(["qb1", "rb1", "wr1", "rb2"], PLAYERS,
                                    values(), self.SLOTS, self.FREE)
        without = trades.lineup_value(["rb1", "wr1", "rb2"], PLAYERS,
                                      values(), self.SLOTS, self.FREE)
        self.assertEqual(whole - without, 2500)

    def test_no_replacement_available_means_the_slot_is_worth_nothing(self):
        without = trades.lineup_value(["rb1", "wr1", "rb2"], PLAYERS,
                                      values(), self.SLOTS, {})
        whole = trades.lineup_value(["qb1", "rb1", "wr1", "rb2"], PLAYERS,
                                    values(), self.SLOTS, {})
        self.assertEqual(whole - without, 5000)

    def test_a_quarterback_may_be_traded_away(self):
        # Forbidding it outright priced the wire at nothing, which is a
        # different mistake from pricing an empty slot at zero.
        found = trades.offers(LEAGUE, Offers.MINE, Offers.THEIRS, PLAYERS,
                              values())
        self.assertTrue(any("qb1" in o["give"] for o in found))

    def test_who_is_free_is_read_from_the_whole_league(self):
        taken = {"qb1", "rb1", "wr1"}
        free = trades.free_agents(values(), taken)
        self.assertEqual(free["QB"], 1000)      # qb2, the only one left
        self.assertEqual(free["RB"], 8000)      # rb2
        self.assertNotIn("K", free)


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


class Offline(unittest.TestCase):
    """Every fetch degrades to nothing rather than raising.

    Twice now an edit has removed a helper that is only reached at call
    time, so importing the module proved nothing and the first sign was a
    NameError in front of the user. These call the paths.
    """

    def silence(self, module):
        real = module._get
        module._get = lambda _url: {"__error__": "NetworkError: offline"}
        self.addCleanup(lambda: setattr(module, "_get", real))

    def test_values_come_back_empty_rather_than_failing(self):
        self.silence(tv)
        self.assertEqual(tv.fetch(), ({}, None))

    def test_the_week_context_comes_back_empty_rather_than_failing(self):
        import nfl_week
        self.silence(nfl_week)
        self.assertEqual(nfl_week.week_context(2026, 1),
                         {"points": {}, "games": {}, "kickoffs": {},
                          "statuses": {}})

    def test_the_schedule_comes_back_empty_rather_than_failing(self):
        import nfl_week
        self.silence(nfl_week)
        self.assertEqual(nfl_week.scoreboard(2026, 1), {})
        self.assertEqual(nfl_week.projection_rows(2026, 1), [])

    def test_a_board_with_no_values_says_so(self):
        self.silence(tv)
        with self.assertRaises(RuntimeError):
            trades.board("someone")


class Pruning(unittest.TestCase):
    """Skipping pairs must not skip offers."""

    def every_pair(self, send, get):
        import itertools
        return ([([a], [b]) for a in send for b in get]
                + [(list(pair), [b])
                   for pair in itertools.combinations(send, 2) for b in get])

    def test_it_finds_everything_the_full_search_would(self):
        # The prune exists to skip work, not answers. Anything inside the
        # fairness band has to survive it.
        v = values()
        send = trades.tradeable(Offers.MINE, PLAYERS, v)
        get = trades.tradeable(Offers.THEIRS, PLAYERS, v)
        free = trades.free_agents(v, set(Offers.MINE["players"])
                                 | set(Offers.THEIRS["players"]))

        def fair(give, take):
            spots = max(0, len(give) - len(take))
            spare = free.get(v.get(take[0], {}).get("position") or "RB", 0.0)
            sent, received = trades.fairness(give, take, v, spots, spare)
            return trades.in_band(sent, received) is not None

        wanted = {(tuple(g), tuple(t))
                  for g, t in self.every_pair(send, get) if fair(g, t)}
        got = {(tuple(g), tuple(t))
               for g, t in trades.candidates(send, get, v, free)}
        self.assertEqual(wanted - got, set())

    def test_it_skips_most_of_them(self):
        v = values()
        send = trades.tradeable(Offers.MINE, PLAYERS, v)
        get = trades.tradeable(Offers.THEIRS, PLAYERS, v)
        free = trades.free_agents(v, set())
        considered = len(list(trades.candidates(send, get, v, free)))
        self.assertLess(considered, len(self.every_pair(send, get)) // 2)

    def test_your_best_for_their_worst_is_never_priced(self):
        # Nobody offers it and nobody accepts it, so neither lineup is
        # worth rebuilding to find that out.
        v = values()
        pairs = list(trades.candidates(
            trades.tradeable(Offers.MINE, PLAYERS, v),
            trades.tradeable(Offers.THEIRS, PLAYERS, v), v, {}))
        self.assertNotIn((["rb1"], ["rb4"]), pairs)


class Fairness(unittest.TestCase):
    """Which side a lopsided package favours, and who gains the spare spot."""

    VALUES = values()

    def test_receiving_more_than_you_send_tilts_your_way(self):
        sent, received = trades.fairness(["rb3"], ["rb1"], self.VALUES, 0, 0)
        self.assertGreater(received, sent)

    def test_the_spare_spot_is_credited_to_the_side_sending_two(self):
        # You send two and get one back, so you are a player short and a
        # spot free; a trade calculator credits that side too.
        plain = trades.fairness(["rb2"], ["wr1"], self.VALUES, 0, 0)[1]
        two_for_one = trades.fairness(["rb2", "rb3"], ["wr1"], self.VALUES,
                                      1, 500)[1]
        self.assertEqual(two_for_one - plain, 500)


class Output(unittest.TestCase):
    """What an offer says about itself."""

    NAMES = {"rb1": {"position": "RB", "full_name": "Spare Back", "team": "GB"},
             "wr1": {"position": "WR", "full_name": "Wanted Man", "team": "KC"},
             "wr2": {"position": "WR", "full_name": "Weak Link", "team": "NYJ"}}

    def offer(self, **kw):
        base = {"give": ["rb1"], "get": ["wr1"], "tilt": 3, "spots": 0,
                "changes": [{"kind": "in", "slot": "WR", "player": "wr1"}]}
        base.update(kw)
        return base

    def test_a_man_joining_the_lineup_is_named_with_his_slot(self):
        self.assertEqual(trades.lineup_changes(self.offer(), self.NAMES),
                         ["WR: Wanted Man (KC WR) in"])

    def test_a_slot_left_empty_is_said_plainly(self):
        offer = self.offer(changes=[{"kind": "empty", "slot": "TE"}])
        self.assertEqual(trades.lineup_changes(offer, self.NAMES),
                         ["TE: left empty"])

    def test_a_man_who_stops_starting_is_named(self):
        offer = self.offer(changes=[{"kind": "benched", "player": "wr2"}])
        self.assertEqual(trades.lineup_changes(offer, self.NAMES),
                         ["Weak Link (NYJ WR) no longer starts"])

    def test_a_close_package_reads_as_even(self):
        self.assertIn("about even", trades.describe(self.offer(), self.NAMES, {}))

    def test_receiving_more_value_reads_as_your_favour(self):
        # tilt is (received - sent), so a positive number is value coming
        # your way. The sentence said the opposite.
        self.assertIn("in your favour",
                      trades.describe(self.offer(tilt=18), self.NAMES, {}))

    def test_sending_more_value_reads_as_their_favour(self):
        self.assertIn("in their favour",
                      trades.describe(self.offer(tilt=-18), self.NAMES, {}))

    def test_the_freed_roster_spot_is_yours(self):
        # Sending two for one leaves you a player short, so the spare spot
        # is on your roster, not theirs.
        said = trades.describe(self.offer(spots=1), self.NAMES, {})
        self.assertIn("leaves you 1 roster spot free", said)

    def test_one_offer_per_player_sent(self):
        # Three variations on trading the same quarterback read as three
        # ideas and are one.
        found = trades.offers(LEAGUE, Offers.MINE, Offers.THEIRS, PLAYERS,
                              values())
        sent = [p for o in found for p in o["give"]]
        self.assertEqual(len(sent), len(set(sent)))


class Stored(unittest.TestCase):
    """Offers are written out once and read back as text."""

    PLAYERS = {"rb1": {"position": "RB", "full_name": "Spare Back",
                       "team": "GB"},
               "wr1": {"position": "WR", "full_name": "Wanted Man",
                       "team": "KC"},
               "wr2": {"position": "WR", "full_name": "Weak Link",
                       "team": "NYJ"}}

    BOARD = {"season": "2026", "week": 3, "source": "FantasyCalc",
             "leagues": [{
                 "league_id": "5", "league_name": "LEHG", "my_record": (2, 1, 0),
                 "summary": "Off to a strong start at 2-1. You are thin at WR.",
                 "settings": {"teams": 10, "ppr": 0.5, "quarterbacks": 1,
                              "pass_td": 6},
                 "offers": [{
                     "give": ["rb1"], "get": ["wr1"], "with": "Their Team",
                     "their_record": (1, 2, 0), "my_pct": 6, "their_pct": 3,
                     "tilt": 4, "spots": 0,
                     "changes": [{"kind": "in", "slot": "WR",
                                  "player": "wr1"}]}]}]}

    def written(self):
        return trades.written_out(self.BOARD, self.PLAYERS)

    def html(self, leagues=None):
        import store as st
        import webapp
        conn = st.connect(":memory:")
        st.write_trade_run(conn, "2026", 3, "FantasyCalc",
                           self.written() if leagues is None else leagues)
        return webapp.render_trades(conn).decode()

    def test_players_are_named_at_save_time(self):
        # So the page needs neither the player database nor a value list.
        offer = self.written()[0]["offers"][0]
        self.assertEqual(offer["send"], ["Spare Back (GB RB)"])
        self.assertEqual(offer["get"], ["Wanted Man (KC WR)"])
        self.assertEqual(offer["their_record"], "1-2")

    def test_the_lineup_change_is_written_out_too(self):
        offer = self.written()[0]["offers"][0]
        self.assertEqual(offer["changes"], ["WR: Wanted Man (KC WR) in"])

    def test_a_stored_run_renders(self):
        html = self.html()
        self.assertIn("Spare Back", html)
        self.assertIn("Wanted Man", html)
        self.assertIn("Their Team", html)

    def test_the_team_summary_is_shown_above_the_offers(self):
        html = self.html()
        self.assertIn("Off to a strong start at 2-1", html)
        self.assertLess(html.index("thin at WR"), html.index("Spare Back"))

    def test_the_league_settings_used_are_stated(self):
        self.assertIn("Priced as 10 teams, 0.5 PPR, 1 QB", self.html())

    def test_scoring_the_value_list_cannot_model_is_flagged(self):
        self.assertIn("6 for a passing touchdown", self.html())

    def test_the_gain_is_a_share_not_a_raw_unit(self):
        self.assertIn("Your lineup +6%", self.html())

    def test_no_run_yet_says_when_they_run(self):
        import store as st
        import webapp
        self.assertIn("They run Tuesday",
                      webapp.render_trades(st.connect(":memory:")).decode())

    def test_a_run_with_no_offers_says_so_plainly(self):
        empty = [dict(self.written()[0], offers=[])]
        self.assertIn("No package makes both sides better", self.html(empty))

    def test_it_says_nothing_is_ever_sent(self):
        self.assertIn("Nothing is ever sent", self.html())

    def test_it_says_byes_are_not_what_offers_are_built_from(self):
        self.assertIn("not for the packages to be built from", self.html())

    def test_a_league_with_nothing_in_it_says_so_rather_than_sitting_blank(self):
        """Now a likelier state than it was, since a fraction of a per cent
        no longer counts as an idea."""
        quiet = [dict(self.BOARD["leagues"][0], offers=[],
                      summary="A paragraph.")]
        quiet[0].pop("shape", None)
        quiet[0].pop("values", None)
        said = self.html(leagues=quiet)
        self.assertIn("worth sending", said)


class OneWeekOutIsNotAHole(unittest.TestCase):
    """The seventh time this project confused two questions.

    waiver_analyzer settled it in writing: "A star out for one week is
    still a star; a star on IR is a roster spot." Whether a man helps you
    on Sunday and whether you want him for the rest of the season are
    different questions, and a trade asks the second.

    This file answered the first. Anyone listed Out lost thirty per cent
    of his trade value, which was enough to change the shape of a roster:
    a quarterback out for one week made the position read as a hole, so
    every single idea in the league became "acquire a quarterback" - one
    of them sending a first-round running back for one. The same mistake
    the other way round made a running back out for one week read as
    expendable, and he was offered around three times in six ideas.
    """

    def hurt(self, pid, status):
        players = {k: dict(v) for k, v in PLAYERS.items()}
        players[pid] = dict(players[pid], injury_status=status)
        return players

    def worth(self, pid, status):
        players = self.hurt(pid, status)
        marked = trades.apply_injuries(values(), players)
        return marked[pid]["value"]

    def test_a_man_out_for_the_week_keeps_his_value(self):
        self.assertEqual(self.worth("qb1", "Out"), 5000.0)

    def test_doubtful_and_questionable_too(self):
        for status in ("Doubtful", "Questionable"):
            with self.subTest(status=status):
                self.assertEqual(self.worth("qb1", status), 5000.0)

    def test_a_season_long_injury_still_costs_him(self):
        for status in ("IR", "PUP", "Sus", "NFI", "DNR"):
            with self.subTest(status=status):
                self.assertLess(self.worth("qb1", status), 5000.0)

    def test_the_statuses_are_waiver_analyzers_and_not_a_second_list(self):
        """Two lists are two chances to make this mistake an eighth time."""
        import waiver_analyzer as wa
        for key in trades.INJURY_DISCOUNT:
            self.assertIn(key, wa.SEASON_STATUSES)

    def test_his_lineup_is_worth_the_same_with_him_out_for_a_week(self):
        """The mechanism, at the point the discount enters the arithmetic.

        Everything downstream is this number: a lineup marked down for a
        one-week absence makes any replacement at that position look like
        a gain.
        """
        ids = ["qb1", "rb1", "rb2", "wr2"]
        slots = trades.starting_slots(LEAGUE)
        healthy = trades.lineup_value(ids, PLAYERS, values(), slots)
        players = self.hurt("qb1", "Out")
        hurt = trades.lineup_value(
            ids, players, trades.apply_injuries(values(), players), slots)
        self.assertEqual(healthy, hurt)

    def test_a_quarterback_out_this_week_is_not_a_hole_to_trade_for(self):
        """The whole league turning into "acquire a quarterback".

        Priced so the discount is the only thing that could flip it: his
        quarterback is the better of the two at full value and the worse
        of them once thirty per cent comes off, so under the old discount
        a downgrade read as an upgrade and the package balanced.
        """
        # A backup on their side, so sending their starter does not empty
        # the slot and sink their own gain.
        worth = values(qb1=9000, qb2=7000)
        worth["qb3"] = {"value": 600.0, "position": "QB", "name": "qb3",
                        "rank": None, "pos_rank": None, "trend": None}
        players = {k: dict(v) for k, v in PLAYERS.items()}
        players["qb3"] = {"position": "QB"}
        players["qb1"]["injury_status"] = "Out"
        mine = {"roster_id": 1,
                "players": ["qb1", "rb1", "rb2", "rb3", "wr2", "wr3"]}
        theirs = {"roster_id": 2, "players": ["qb2", "qb3", "wr1", "wr4"]}
        marked = trades.apply_injuries(worth, players)
        found = trades.offers(LEAGUE, mine, theirs, players, marked)
        self.assertTrue(found, "there is still a trade to find")
        for offer in found:
            self.assertNotIn(
                "qb2", offer["get"],
                "a quarterback out for one week is not a hole at "
                "quarterback")

    def test_a_back_out_this_week_is_not_the_one_to_send(self):
        """The same mistake seen from the other side."""
        mine = {"roster_id": 1,
                "players": ["qb1", "rb1", "rb2", "rb3", "wr2", "wr3"]}
        theirs = {"roster_id": 2, "players": ["qb2", "wr1", "wr4", "rb4"]}
        players = self.hurt("rb1", "Out")
        marked = trades.apply_injuries(values(), players)
        healthy = trades.offers(LEAGUE, mine, theirs, PLAYERS, values())
        found = trades.offers(LEAGUE, mine, theirs, players, marked)
        self.assertEqual([o["give"] for o in found],
                         [o["give"] for o in healthy],
                         "one week out changed who is expendable")

    def test_the_summary_does_not_call_a_one_week_absence_a_need(self):
        """"counts as need at that position rather than depth" is a claim
        about the rest of the season, so only a season injury earns it."""
        mine = {"roster_id": 1,
                "players": ["qb1", "rb1", "rb2", "rb3", "wr2", "wr3"]}
        players = self.hurt("rb1", "Out")
        got = trades.shape(LEAGUE, mine, players,
                           trades.apply_injuries(values(), players))
        self.assertEqual(got["hurt"], [])

    def test_but_a_season_injury_is_still_called_one(self):
        mine = {"roster_id": 1,
                "players": ["qb1", "rb1", "rb2", "rb3", "wr2", "wr3"]}
        players = self.hurt("rb1", "IR")
        got = trades.shape(LEAGUE, mine, players,
                           trades.apply_injuries(values(), players))
        self.assertEqual([pid for pid, _pos, _s in got["hurt"]], ["rb1"])


class WhatActuallyChangedInTheLineup(unittest.TestCase):
    """A reshuffle is not a change, and the old diff said it was.

    Comparing slot index against slot index turned two receivers swapping
    places into a chain of lines where the same man was "in" on one and
    "out" on the next. Four lines, three contradicting the line above,
    for a trade that moved one player.
    """

    SLOTS = ["QB", "WR", "WR", "FLEX", "FLEX"]

    def test_a_reshuffle_of_the_same_men_is_no_change_at_all(self):
        before = {0: "qb1", 1: "wr2", 2: "wr3", 3: "rb2", 4: "rb3"}
        after = {0: "qb1", 1: "wr3", 2: "wr2", 3: "rb3", 4: "rb2"}
        self.assertEqual(
            trades.lineup_delta(before, after, self.SLOTS), [])

    def test_a_man_joining_is_reported_once_with_the_slot_he_fills(self):
        before = {0: "qb1", 1: "wr2", 2: "wr3", 3: "rb2", 4: "rb3"}
        after = {0: "qb2", 1: "wr3", 2: "wr2", 3: "rb3", 4: "rb2"}
        got = trades.lineup_delta(before, after, self.SLOTS, sent=["qb1"])
        self.assertEqual(got, [{"kind": "in", "slot": "QB",
                                "player": "qb2"}])

    def test_the_man_you_send_is_not_named_a_second_time_as_a_loss(self):
        before = {0: "qb1", 1: "wr2", 2: "wr3", 3: "rb2", 4: "rb3"}
        after = {0: "qb1", 1: "wr1", 2: "wr3", 3: "rb2", 4: "rb3"}
        got = trades.lineup_delta(before, after, self.SLOTS, sent=["wr2"])
        self.assertEqual([c["kind"] for c in got], ["in"])

    def test_a_starter_pushed_to_the_bench_is_the_hidden_cost_and_is_said(self):
        before = {0: "qb1", 1: "wr2", 2: "wr3", 3: "rb2", 4: "rb3"}
        after = {0: "qb1", 1: "wr1", 2: "wr3", 3: "rb2", 4: "rb3"}
        got = trades.lineup_delta(before, after, self.SLOTS, sent=[])
        self.assertIn({"kind": "benched", "player": "wr2"}, got)

    def test_an_emptied_slot_is_said_first_and_said_plainly(self):
        before = {0: "qb1", 1: "wr2", 2: "wr3", 3: "rb2", 4: "rb3"}
        after = {0: None, 1: "wr2", 2: "wr3", 3: "rb2", 4: "rb3"}
        got = trades.lineup_delta(before, after, self.SLOTS, sent=["qb1"])
        self.assertEqual(got[0], {"kind": "empty", "slot": "QB"})

    def test_a_real_offer_never_names_a_man_both_in_and_out(self):
        """The shape of the bug, asserted against the real search."""
        mine = {"roster_id": 1,
                "players": ["qb1", "rb1", "rb2", "rb3", "wr2", "wr3"]}
        theirs = {"roster_id": 2, "players": ["qb2", "wr1", "wr4", "rb4"]}
        league = dict(LEAGUE,
                      roster_positions=["QB", "WR", "WR", "FLEX", "FLEX",
                                        "BN", "BN"])
        found = trades.offers(league, mine, theirs, PLAYERS, values())
        self.assertTrue(found)
        for offer in found:
            joined = [c["player"] for c in offer["changes"]
                      if c["kind"] == "in"]
            left = [c["player"] for c in offer["changes"]
                    if c["kind"] == "benched"]
            self.assertEqual(set(joined) & set(left), set())
            self.assertEqual(len(joined), len(set(joined)))

    def test_every_change_it_makes_renders_to_a_line(self):
        """A kind nobody renders is a change the reader never sees."""
        mine = {"roster_id": 1,
                "players": ["qb1", "rb1", "rb2", "rb3", "wr2", "wr3"]}
        theirs = {"roster_id": 2, "players": ["qb2", "wr1", "wr4", "rb4"]}
        for offer in trades.offers(LEAGUE, mine, theirs, PLAYERS, values()):
            self.assertEqual(len(trades.lineup_changes(offer, PLAYERS)),
                             len(offer["changes"]))


class WorthSending(unittest.TestCase):
    """Better by any margin is not the same as worth a message.

    The gate was my_gain > 0, so an offer that improved the lineup by a
    third of a per cent was a proposal. Three of five ideas in The Minor
    League read "your lineup +0%" - each of them true, positive, and no
    reason to message anybody. A league with little to do should say so
    rather than fill the space.
    """

    MINE = {"roster_id": 1,
            "players": ["qb1", "rb1", "rb2", "rb3", "wr2", "wr3"]}
    THEIRS = {"roster_id": 2, "players": ["qb2", "wr1", "wr4", "rb4"]}

    def find(self, **kw):
        return trades.offers(LEAGUE, self.MINE, self.THEIRS, PLAYERS,
                             values(**kw))

    def test_nothing_it_offers_is_shown_as_no_gain_at_all(self):
        """The floor is the displayed number, so the two cannot disagree."""
        for offer in self.find():
            self.assertGreaterEqual(offer["my_pct"], trades.WORTH_SENDING)

    def test_a_trade_worth_making_still_surfaces(self):
        self.assertTrue(self.find())

    # A roster deep enough that losing its flex costs almost nothing, and
    # an opponent with a receiver a shade better than the man he replaces.
    # Both packages below pass every other gate - fair by value, both
    # lineups up - and one of them is worth a third of a per cent.
    THIN_PICKINGS = {
        "mine": {"roster_id": 1,
                 "players": ["qb1", "rb1", "rb2", "rb4", "wr2", "wr3"]},
        "theirs": {"roster_id": 2, "players": ["qb2", "wr1", "rb3"]},
        "worth": {"qb1": 6000, "rb1": 9000, "rb2": 8000, "rb4": 7950,
                  "wr2": 7000, "wr3": 6000, "qb2": 5000, "wr1": 7200,
                  "rb3": 200},
    }

    def slim(self, floor):
        real = trades.WORTH_SENDING
        trades.WORTH_SENDING = floor
        try:
            return trades.offers(LEAGUE, self.THIN_PICKINGS["mine"],
                                 self.THIN_PICKINGS["theirs"], PLAYERS,
                                 values(**self.THIN_PICKINGS["worth"]))
        finally:
            trades.WORTH_SENDING = real

    def test_the_old_gate_called_a_third_of_a_per_cent_an_idea(self):
        """Without this, the test below proves only that nothing was
        found - which an over-tight fairness band would also do."""
        found = self.slim(0)
        self.assertIn(0, [o["my_pct"] for o in found])

    def test_and_now_it_is_dropped(self):
        self.assertNotIn(0, [o["my_pct"] for o in self.slim(1)])

    def test_while_the_real_gain_beside_it_survives(self):
        self.assertEqual([o["give"] for o in self.slim(1)], [["rb4"]])

    def test_the_other_side_gaining_little_is_not_held_against_it(self):
        """An offer they barely improve on but gain value from is one they
        may well take, and it is the best kind for you. So the floor is
        read against your lineup only."""
        # A strong roster on their side, so a real gain is a small share
        # of it - which is how several live ideas read "theirs +0%".
        worth = dict(self.THIN_PICKINGS["worth"], rb3=7000, wr4=7900)
        theirs = {"roster_id": 2,
                  "players": ["qb2", "wr1", "rb3", "wr4"]}
        found = trades.offers(LEAGUE, self.THIN_PICKINGS["mine"], theirs,
                              PLAYERS, values(**worth))
        kept = [o for o in found if o["their_pct"] < trades.WORTH_SENDING]
        self.assertTrue(kept, "an offer worth little to them still stands")
        for offer in kept:
            self.assertGreater(offer["their_gain"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
