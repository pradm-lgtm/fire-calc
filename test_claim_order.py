#!/usr/bin/env python3
"""Tests for the order waiver claims are filed in.

    python3 test_claim_order.py

The order is the whole meaning of a fallback. Two claims for the same player
filed the wrong way round do not fail safely: you get the one you wanted
least, or both and a roster you did not ask for.
"""

import unittest

import claim_order as co
import claim_safety as cs
import sleeper_client as sc
import store as st
import webapp


def claim(cid, add, drop, bid=0, priority=0, league="L1"):
    return {"id": cid, "league_id": league,
            "add_player_id": add, "add_player_name": f"{add} (SF WR)",
            "drop_player_id": drop,
            "drop_player_name": f"{drop} (NYJ RB)" if drop else None,
            "bid": bid, "rank": 0, "priority": priority}


class Order(unittest.TestCase):

    def test_the_dearest_bid_leads_until_somebody_says_otherwise(self):
        rows = [claim(1, "a", "d", bid=3), claim(2, "b", "e", bid=9)]
        self.assertEqual([r["id"] for r in co.ordered(rows)], [2, 1])

    def test_a_hand_set_order_beats_the_bids(self):
        rows = [claim(1, "a", "d", bid=3, priority=1),
                claim(2, "b", "e", bid=9, priority=2)]
        self.assertEqual([r["id"] for r in co.ordered(rows)], [1, 2])

    def test_leagues_are_kept_apart(self):
        rows = [claim(1, "a", "d", league="L1"),
                claim(2, "a", "d", league="L2")]
        self.assertEqual(co.blockers(rows) and True, True)  # same list clashes
        groups = co.by_league(rows)
        self.assertEqual(sorted(groups), ["L1", "L2"])
        # But claim by claim, nothing in L1 can block anything in L2.
        self.assertEqual(co.blockers(groups["L1"]), {})

    def test_a_row_missing_a_column_still_sorts(self):
        # The submitter reads its queue from the host as JSON.
        thin = {"id": 4, "league_id": "L1", "add_player_id": "a",
                "add_player_name": "a", "drop_player_id": None,
                "drop_player_name": None, "bid": 5}
        self.assertEqual(co.ordered([thin]), [thin])


class Fallbacks(unittest.TestCase):

    def test_sharing_a_drop_makes_the_lower_one_a_fallback(self):
        rows = [claim(1, "black", "dobbins", bid=20),
                claim(2, "mayer", "dobbins", bid=5)]
        found = co.blockers(rows)
        self.assertEqual(list(found), [2])
        self.assertIn("both drop dobbins", found[2][1])

    def test_the_first_claim_is_never_a_fallback(self):
        rows = [claim(1, "black", "dobbins", bid=20),
                claim(2, "mayer", "dobbins", bid=5)]
        self.assertNotIn(1, co.blockers(rows))

    def test_adding_the_same_player_twice_is_a_fallback_too(self):
        # Mayer for Dobbins, else Mayer for somebody else.
        rows = [claim(1, "mayer", "dobbins", bid=8),
                claim(2, "mayer", "pierce", bid=8, priority=2)]
        found = co.blockers(rows)
        self.assertIn("cannot add", found[2][1])

    def test_independent_claims_are_left_alone(self):
        rows = [claim(1, "black", "dobbins"), claim(2, "mayer", "pierce")]
        self.assertEqual(co.blockers(rows), {})

    def test_a_chain_points_at_the_one_directly_above(self):
        rows = [claim(1, "a", "d", priority=1), claim(2, "b", "d", priority=2),
                claim(3, "c", "d", priority=3)]
        found = co.blockers(rows)
        self.assertEqual(found[2][0]["id"], 1)
        self.assertEqual(found[3][0]["id"], 2)

    def test_two_claims_with_no_drop_at_all_do_not_clash(self):
        rows = [claim(1, "a", None), claim(2, "b", None)]
        self.assertEqual(co.blockers(rows), {})


class Moving(unittest.TestCase):

    def test_up_swaps_with_the_one_above(self):
        rows = [claim(1, "a", "d", priority=1), claim(2, "b", "e", priority=2)]
        self.assertEqual(co.moved(rows, 2, "up"), [2, 1])

    def test_off_the_end_changes_nothing(self):
        rows = [claim(1, "a", "d", priority=1), claim(2, "b", "e", priority=2)]
        self.assertEqual(co.moved(rows, 1, "up"), [1, 2])
        self.assertEqual(co.moved(rows, 2, "down"), [1, 2])


class Stored(unittest.TestCase):
    """The order as the database and the page keep it."""

    def build(self, *overrides):
        conn = st.connect(":memory:")
        run_id = st.start_run(conn, "2026", 2, ["FantasyPros"])
        base = dict(league_id="1", league_name="OTG Alumni",
                    add_player_id="1", add_player_name="Kaelon Black (IND RB)",
                    add_position="RB", drop_player_id="d1",
                    drop_player_name="JK Dobbins (DEN RB)", drop_position="RB",
                    bid=6, max_bid=100, consensus=1, sources=[],
                    rationale="", quote="",
                    drop_options=[{"id": "d1", "name": "JK Dobbins (DEN RB)",
                                   "position": "RB", "why": "RB"},
                                  {"id": "d2", "name": "Alec Pierce (IND WR)",
                                   "position": "WR", "why": "WR"}])
        ids = []
        for i, over in enumerate(overrides or [{}]):
            row = dict(base, add_player_id=str(10 + i))
            row.update(over)
            ids.append(st.add_proposal(conn, run_id, **row))
        return conn, run_id, ids

    def test_the_queue_comes_back_dearest_first(self):
        conn, run_id, _ = self.build({"bid": 4}, {"bid": 12}, {"bid": 7})
        self.assertEqual([r["bid"] for r in st.proposals_for_run(conn, run_id)],
                         [12, 7, 4])

    def test_moving_one_up_sticks(self):
        conn, run_id, ids = self.build({"bid": 12}, {"bid": 4})
        self.assertTrue(st.reorder(conn, ids[1], "up"))
        self.assertEqual([r["id"] for r in st.proposals_for_run(conn, run_id)],
                         [ids[1], ids[0]])

    def test_the_hand_set_order_survives_a_change_of_bid(self):
        # Every claim is renumbered, not just the two that swapped, so the
        # order stops depending on the bids that produced it.
        conn, run_id, ids = self.build({"bid": 12}, {"bid": 4})
        st.reorder(conn, ids[1], "up")
        st.decide(conn, ids[0], st.APPROVED, bid=99)
        self.assertEqual([r["id"] for r in st.proposals_for_run(conn, run_id)],
                         [ids[1], ids[0]])

    def test_moving_off_the_end_reports_nothing_moved(self):
        conn, _run, ids = self.build({"bid": 12}, {"bid": 4})
        self.assertFalse(st.reorder(conn, ids[0], "up"))

    def test_a_submitted_claim_cannot_be_reordered(self):
        conn, _run, ids = self.build({"bid": 12}, {"bid": 4})
        st.mark_submitted(conn, ids[1], True, "placed")
        self.assertFalse(st.reorder(conn, ids[1], "up"))

    def test_a_fallback_lands_directly_below_its_original(self):
        conn, run_id, ids = self.build({"bid": 12}, {"bid": 4})
        new = st.add_fallback(conn, ids[0])
        self.assertEqual([r["id"] for r in st.proposals_for_run(conn, run_id)],
                         [ids[0], new, ids[1]])

    def test_a_fallback_cuts_somebody_else(self):
        conn, _run, ids = self.build()
        new = st.add_fallback(conn, ids[0])
        rows = {r["id"]: r for r in st.proposals_for_run(conn, _run)}
        self.assertEqual(rows[ids[0]]["drop_player_id"], "d1")
        self.assertEqual(rows[new]["drop_player_id"], "d2")
        self.assertEqual(rows[new]["add_player_id"],
                         rows[ids[0]]["add_player_id"])

    def test_no_fallback_when_there_is_nobody_else_to_cut(self):
        conn, _run, ids = self.build({"drop_options": [
            {"id": "d1", "name": "JK Dobbins (DEN RB)", "position": "RB"}]})
        self.assertIsNone(st.add_fallback(conn, ids[0]))

    def test_the_submitter_gets_them_in_order(self):
        conn, _run, ids = self.build({"bid": 4}, {"bid": 12})
        for pid in ids:
            st.decide(conn, pid, st.APPROVED)
        st.reorder(conn, ids[0], "up")
        self.assertEqual([r["id"] for r in st.approved_unsubmitted(conn)], ids)

    def test_the_page_says_which_claim_is_the_fallback(self):
        conn, _run, ids = self.build()
        st.add_fallback(conn, ids[0])
        html = webapp.render(conn).decode()
        self.assertIn("Fallback", html)
        self.assertIn("Runs only if", html)

    def test_only_claims_in_a_chain_are_given_an_order(self):
        # Bids decide who wins a player, so a claim competing with nothing
        # is in no position and is not shown arrows implying it is.
        conn, _run, ids = self.build(
            {"bid": 12},
            {"bid": 4, "drop_player_id": "d2",
             "drop_player_name": "Alec Pierce (IND WR)"})
        self.assertNotIn("action='/order'", webapp.render(conn).decode())
        st.add_fallback(conn, ids[0])
        html = webapp.render(conn).decode()
        self.assertIn("action='/order'", html)
        self.assertIn("First choice", html)

    def test_one_claim_alone_gets_no_ordering_controls(self):
        conn, _run, _ids = self.build()
        self.assertNotIn("action='/order'", webapp.render(conn).decode())

    def test_the_move_buttons_say_what_they_do(self):
        conn, _run, ids = self.build()
        st.add_fallback(conn, ids[0])
        self.assertIn("Move up", webapp.render(conn).decode())

    def test_the_submit_panel_shows_the_running_order(self):
        conn, _run, ids = self.build({"bid": 12}, {"bid": 4})
        for pid in ids:
            st.decide(conn, pid, st.APPROVED)
        html = webapp.render(conn).decode()
        self.assertIn("class='queue'", html)


class Telling(unittest.TestCase):
    """Reading back a claim when two of them add the same player."""

    ROSTERS = [{"owner_id": "me", "roster_id": 3, "starters": ["s1"],
                "players": ["s1", "d1", "d2"],
                "settings": {"waiver_budget_used": 0}}]

    def setUp(self):
        self.real_rosters = sc.league_rosters
        self.real_claims = cs.waiver_claims
        sc.league_rosters = lambda _lid: self.ROSTERS

    def tearDown(self):
        sc.league_rosters = self.real_rosters
        cs.waiver_claims = self.real_claims

    def proposal(self, drop):
        return {"league_id": "1", "add_player_id": "a1",
                "add_player_name": "Michael Mayer (LV TE)",
                "drop_player_id": drop, "drop_player_name": drop,
                "bid": 5, "max_bid": 100}

    def test_the_right_one_of_two_claims_for_the_same_player(self):
        cs.waiver_claims = lambda _lid, _wk: [
            {"week": 3, "transaction_id": "t1", "status": "complete",
             "roster_ids": [3], "adds": {"a1": 3}, "drops": {"d1": 3},
             "bid": 5},
            {"week": 3, "transaction_id": "t2", "status": "complete",
             "roster_ids": [3], "adds": {"a1": 3}, "drops": {"d2": 3},
             "bid": 5},
        ]
        found, detail = cs.verify_submitted(None, self.proposal("d2"), "me", 3)
        self.assertTrue(found)
        self.assertIn("complete", detail)

    def test_add_only_matching_still_works_when_nothing_matches_the_drop(self):
        cs.waiver_claims = lambda _lid, _wk: [
            {"week": 3, "transaction_id": "t1", "status": "complete",
             "roster_ids": [3], "adds": {"a1": 3}, "drops": {"d1": 3},
             "bid": 5}]
        # Only one claim exists and it cuts somebody else, so it is still
        # the claim being asked about - the add is what identifies it.
        found, detail = cs.verify_submitted(None, self.proposal("d2"), "me", 3)
        self.assertTrue(found)
        self.assertIn("complete", detail)

    def test_a_fallback_passes_preflight_while_its_original_is_pending(self):
        # Both claims cut the same player, and he is still on the roster
        # because nothing has processed yet. Both must be placeable.
        ok, why, _settled = cs.preflight(self.proposal("d1"), "me")
        self.assertTrue(ok, why)


if __name__ == "__main__":
    unittest.main(verbosity=2)
