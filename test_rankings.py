#!/usr/bin/env python3
"""Tests for reading rankings out of the data a page ships.

    python3 test_rankings.py

The extractor is worth testing because its failures are silent: a page that
parses to nothing looks exactly like a page with no rankings on it, and the
last three versions of this code each returned an empty list on every real
page while passing a casual eyeball check.
"""

import unittest

import rankings as rk

PADDING = "x" * 300


def script(body):
    return f"<html><body><script>{body}</script></body></html>"


class EmbeddedRows(unittest.TestCase):

    def test_assignment_not_at_end_of_script(self):
        # The shape that actually broke: matching up to </script> finds
        # nothing, because the ranking blob is never the last statement.
        html = script(
            'var ecrData = {"sport":"NFL","players":['
            '{"player_name":"Ja\'Marr Chase","rank_ecr":1},'
            '{"player_name":"CeeDee Lamb","rank_ecr":2},'
            f'{{"player_name":"Puka Nacua","rank_ecr":3}}],"note":"{PADDING}"}};'
            'var sosData = {"a":1};')
        self.assertEqual(
            rk.ranked_rows_from_html(html),
            [("Ja'Marr Chase", 1.0), ("CeeDee Lamb", 2.0), ("Puka Nacua", 3.0)])

    def test_braces_and_quotes_inside_strings(self):
        html = script(
            'var d = {"players":['
            '{"player_name":"A \\" {B}","rank_ecr":1},'
            f'{{"player_name":"C","rank_ecr":2}}],"pad":"{PADDING}"}};')
        self.assertEqual([n for n, _r in rk.ranked_rows_from_html(html)],
                         ['A " {B}', "C"])

    def test_bare_json_script_tag(self):
        html = ('<script type="application/json">'
                '{"players":[{"name":"A","rank":2},{"name":"B","rank":1}],'
                f'"pad":"{PADDING}"}}</script>')
        self.assertEqual(rk.ranked_rows_from_html(html),
                         [("B", 1.0), ("A", 2.0)])

    def test_largest_blob_wins(self):
        html = (script(f'var small = {{"players":[{{"name":"A","rank":1}}],'
                       f'"pad":"{PADDING}"}};')
                + script('var big = {"players":['
                         '{"name":"B","rank":1},{"name":"C","rank":2},'
                         f'{{"name":"D","rank":3}}],"pad":"{PADDING}"}};'))
        self.assertEqual([n for n, _r in rk.ranked_rows_from_html(html)],
                         ["B", "C", "D"])

    def test_repeated_player_keeps_best_rank(self):
        html = script('var d = {"players":['
                      '{"name":"A","rank":5},{"name":"B","rank":2},'
                      f'{{"name":"A","rank":1}}],"pad":"{PADDING}"}};')
        self.assertEqual(rk.ranked_rows_from_html(html),
                         [("A", 1.0), ("B", 2.0)])

    def test_no_data_is_empty_not_an_error(self):
        self.assertEqual(rk.ranked_rows_from_html(""), [])
        self.assertEqual(rk.ranked_rows_from_html("<html><body>hi</body></html>"), [])
        self.assertEqual(rk.ranked_rows_from_html(script("var x = 1;")), [])

    def test_unterminated_json_does_not_hang(self):
        self.assertEqual(rk.ranked_rows_from_html(script(
            'var d = {"players":[{"name":"A","rank":1}' + PADDING)), [])


class RowsToRanks(unittest.TestCase):

    PLAYERS = {
        "1": {"position": "WR", "full_name": "Ja'Marr Chase"},
        "2": {"position": "WR", "full_name": "CeeDee Lamb"},
        "3": {"position": "RB", "full_name": "Bijan Robinson"},
    }
    GAZETTEER = {"jamarr chase": "1", "ceedee lamb": "2",
                 "bijan robinson": "3"}

    def test_gaps_in_source_ranks_become_dense_positional_ranks(self):
        rows = [("CeeDee Lamb", 14.0), ("Ja'Marr Chase", 3.0),
                ("Bijan Robinson", 8.0)]
        got = rk.ranks_from_rows(rows, self.PLAYERS, self.GAZETTEER,
                                 positions=["WR"])
        self.assertEqual(got, {"WR": {"1": 1, "2": 2}})

    def test_overall_orders_positions_against_each_other(self):
        rows = [("CeeDee Lamb", 14.0), ("Ja'Marr Chase", 3.0),
                ("Bijan Robinson", 8.0)]
        got = rk.ranks_from_rows(rows, self.PLAYERS, self.GAZETTEER,
                                 positions=["WR", "RB"], overall=True)
        self.assertEqual(got[rk.OVERALL], {"1": 1, "3": 2, "2": 3})


if __name__ == "__main__":
    unittest.main(verbosity=2)
