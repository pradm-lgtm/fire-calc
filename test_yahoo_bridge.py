#!/usr/bin/env python3
"""Matching Yahoo's players to the Sleeper records the model runs on.

The value model - projections, ranks, keep and add values - is keyed by
Sleeper id. Without this the Yahoo leagues get a second, weaker model or
nothing at all, and today they get nothing.
"""

import unittest

import yahoo_bridge as yb


PLAYERS = {
    "1": {"full_name": "C.J. Stroud", "position": "QB", "team": "HOU"},
    "2": {"full_name": "Michael Thomas", "position": "WR", "team": "NO"},
    "3": {"full_name": "Michael Thomas", "position": "WR", "team": "LAR"},
    "4": {"first_name": "Amon-Ra", "last_name": "St. Brown",
          "position": "WR", "team": "DET"},
    "5": {"full_name": "Marvin Harrison Jr.", "position": "WR", "team": "ARI"},
    "6": {"full_name": "Travis Etienne", "position": "RB", "team": "JAX"},
    "SF": {"full_name": "San Francisco 49ers", "position": "DEF",
           "team": "SF"},
}


def yahoo(name, pos, team=None, key="470.p.1"):
    return {"name": name, "position": pos, "team": team, "key": key}


class Matching(unittest.TestCase):

    def setUp(self):
        self.idx = yb.index_sleeper(PLAYERS)

    def found(self, who):
        return yb.match(who, self.idx, PLAYERS)[0]

    def why_not(self, who):
        return yb.match(who, self.idx, PLAYERS)[1]

    def test_the_straightforward_case(self):
        self.assertEqual(self.found(yahoo("C.J. Stroud", "QB", "Hou")), "1")

    def test_punctuation_does_not_matter(self):
        """Yahoo and Sleeper do not agree on where the dots go."""
        for spelling in ("CJ Stroud", "C. J. Stroud", "C.J. Stroud"):
            with self.subTest(spelling=spelling):
                self.assertEqual(self.found(yahoo(spelling, "QB", "Hou")), "1")

    def test_a_suffix_does_not_matter(self):
        self.assertEqual(
            self.found(yahoo("Marvin Harrison", "WR", "Ari")), "5")
        self.assertEqual(
            self.found(yahoo("Marvin Harrison Jr.", "WR", "Ari")), "5")

    def test_a_name_built_from_two_fields_is_still_found(self):
        self.assertEqual(
            self.found(yahoo("Amon-Ra St. Brown", "WR", "Det")), "4")

    def test_case_of_the_team_does_not_matter(self):
        self.assertEqual(self.found(yahoo("C.J. Stroud", "QB", "hou")), "1")

    def test_a_shared_name_is_settled_by_team(self):
        self.assertEqual(self.found(yahoo("Michael Thomas", "WR", "NO")), "2")
        self.assertEqual(self.found(yahoo("Michael Thomas", "WR", "LAR")), "3")

    def test_a_shared_name_with_no_team_is_refused_not_guessed(self):
        """A wrong player means a claim for somebody you did not ask for.

        A missing one means a quieter card. Those costs are not close, so
        this takes the second every time.
        """
        who = yahoo("Michael Thomas", "WR", None)
        self.assertIsNone(self.found(who))
        self.assertIn("share that name", self.why_not(who))

    def test_a_position_yahoo_and_sleeper_disagree_about_still_matches(self):
        """Most often a man who moves between back and receiver."""
        self.assertEqual(self.found(yahoo("Travis Etienne", "WR", "Jax")), "6")

    def test_somebody_sleeper_has_never_heard_of(self):
        who = yahoo("Nobody At All", "RB", "GB")
        self.assertIsNone(self.found(who))
        self.assertIn("no Sleeper player", self.why_not(who))

    def test_an_empty_name_is_refused(self):
        self.assertIsNone(self.found(yahoo("", "RB", "GB")))


class InitialsWrittenWithSpaces(unittest.TestCase):
    """Yahoo writes "C. J. Stroud"; stripping the dots leaves three words.

    expert_extract's normaliser turns that into "c j stroud" and "CJ
    Stroud" into "cj stroud", so they never meet and a real player goes
    unmatched for the sake of a space.
    """

    def test_the_spellings_all_agree(self):
        keys = {yb.normal(n) for n in
                ("C. J. Stroud", "C.J. Stroud", "CJ Stroud", "C J Stroud")}
        self.assertEqual(len(keys), 1)

    def test_three_initials_too(self):
        self.assertEqual(yb.normal("A. J. B. Smith"), yb.normal("AJB Smith"))

    def test_an_ordinary_name_is_left_alone(self):
        self.assertEqual(yb.normal("Saquon Barkley"), "saquon barkley")

    def test_a_short_first_name_is_not_glued_to_the_surname(self):
        """"Bo Nix" must not become "bonix"."""
        self.assertEqual(yb.normal("Bo Nix"), "bo nix")

    def test_a_suffix_still_goes(self):
        self.assertEqual(yb.normal("Marvin Harrison Jr."), "marvin harrison")

    def test_nothing_is_nothing(self):
        self.assertEqual(yb.normal(""), "")
        self.assertEqual(yb.normal(None), "")


class Defenses(unittest.TestCase):
    """Sleeper keys a defense by team; Yahoo names the city."""

    def setUp(self):
        self.idx = yb.index_sleeper(PLAYERS)

    def test_a_defense_matches_on_its_team_not_its_name(self):
        """"San Francisco" and "San Francisco 49ers" never meet as names."""
        got, _why = yb.match(yahoo("San Francisco", "DEF", "SF"), self.idx,
                             PLAYERS)
        self.assertEqual(got, "SF")

    def test_a_defense_with_no_team_is_refused(self):
        got, why = yb.match(yahoo("Somebody", "DEF", None), self.idx, PLAYERS)
        self.assertIsNone(got)
        self.assertIn("defense", why)

    def test_a_team_sleeper_has_no_defense_for(self):
        got, why = yb.match(yahoo("Toronto", "DEF", "TOR"), self.idx, PLAYERS)
        self.assertIsNone(got)
        self.assertIn("defense", why)


class TeamCodes(unittest.TestCase):

    def test_the_ones_they_spell_the_same(self):
        for code in ("HOU", "NO", "NYJ", "LV", "KC"):
            self.assertEqual(yb.team_code(code.lower()), code)

    def test_the_ones_they_do_not(self):
        self.assertEqual(yb.team_code("WSH"), "WAS")
        self.assertEqual(yb.team_code("JAC"), "JAX")

    def test_a_team_that_moved(self):
        self.assertEqual(yb.team_code("SD"), "LAC")
        self.assertEqual(yb.team_code("OAK"), "LV")

    def test_nothing_is_nothing(self):
        self.assertEqual(yb.team_code(None), "")
        self.assertEqual(yb.team_code(""), "")


class TheWholeRoster(unittest.TestCase):

    def test_it_returns_what_matched_and_what_did_not(self):
        squad = [yahoo("C.J. Stroud", "QB", "Hou", "470.p.1"),
                 yahoo("Nobody At All", "RB", "GB", "470.p.2")]
        found, missed = yb.bridge(squad, PLAYERS)
        self.assertEqual(found, {"470.p.1": "1"})
        self.assertEqual(len(missed), 1)
        self.assertEqual(missed[0][0]["name"], "Nobody At All")

    def test_an_empty_roster_is_not_an_error(self):
        self.assertEqual(yb.bridge([], PLAYERS), ({}, []))


class SayingHowWellItDid(unittest.TestCase):
    """A bridge nobody measured is what this project keeps getting caught by."""

    def said(self, found, missed):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            yb.report(found, missed, "your roster")
        return out.getvalue()

    def test_it_prints_the_rate(self):
        said = self.said({"a": "1", "b": "2", "c": "3"},
                         [(yahoo("Missing Man", "WR", "GB"), "no Sleeper")])
        self.assertIn("3 of 4", said)
        self.assertIn("75%", said)

    def test_it_names_every_miss(self):
        """96% is not reassuring if the 4% is your starting lineup."""
        said = self.said({}, [(yahoo("Missing Man", "WR", "GB"), "no such")])
        self.assertIn("Missing Man", said)
        self.assertIn("no such", said)

    def test_nobody_to_match_is_said_rather_than_divided_by_zero(self):
        self.assertIn("nobody to match", self.said({}, []))


class TheCheckItself(unittest.TestCase):
    """Driven with stubs, because the calls it makes are the thing that broke.

    my_teams() takes no arguments and answers for every league at once;
    it was being called with a league key. Nothing caught that - the
    entrypoint import check cannot see a bad call inside a function, and
    every other test here exercised the matching rather than the command.
    """

    def setUp(self):
        import sleeper_client as sc
        import yahoo_client as yc
        self.yc, self.sc = yc, sc
        self.real = (yc.my_leagues, yc.my_teams, yc.roster, yc.free_agents,
                     sc.all_players)
        yc.my_leagues = lambda: [
            {"key": "470.l.715420", "name": "The Minor League", "week": 3}]
        yc.my_teams = lambda: [
            {"key": "470.l.715420.t.2", "name": "Slim Pickens",
             "league": "470.l.715420"},
            {"key": "470.l.1533743.t.1", "name": "Team Ruhi",
             "league": "470.l.1533743"}]
        yc.roster = lambda key, week=None: [
            {"name": "C.J. Stroud", "position": "QB", "team": "Hou",
             "key": "470.p.40030"}]
        yc.free_agents = lambda key, count=50: [
            {"name": "Nobody At All", "position": "RB", "team": "GB",
             "key": "470.p.9"}]
        sc.all_players = lambda refresh=False: PLAYERS

    def tearDown(self):
        (self.yc.my_leagues, self.yc.my_teams, self.yc.roster,
         self.yc.free_agents, self.sc.all_players) = self.real

    def said(self, league_key):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = yb.check(league_key)
        return code, out.getvalue()

    def test_it_runs_end_to_end(self):
        code, said = self.said("470.l.715420")
        self.assertEqual(code, 0)
        self.assertIn("The Minor League", said)

    def test_it_picks_your_team_in_that_league_not_the_other_one(self):
        _code, said = self.said("470.l.715420")
        self.assertIn("your roster", said)

    def test_it_reports_both_the_roster_and_the_wire(self):
        _code, said = self.said("470.l.715420")
        self.assertIn("your roster: 1 of 1", said)
        self.assertIn("the wire: 0 of 1", said)

    def test_a_league_that_is_not_yours_is_refused(self):
        code, said = self.said("470.l.999999")
        self.assertEqual(code, 1)
        self.assertIn("not one of your leagues", said)

    def test_the_attribution_yahoo_asked_for_is_printed(self):
        _code, said = self.said("470.l.715420")
        self.assertIn("Yahoo", said)


if __name__ == "__main__":
    unittest.main(verbosity=2)
