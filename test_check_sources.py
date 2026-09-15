#!/usr/bin/env python3
"""Tests for judging a candidate source.

    python3 test_check_sources.py

A hub is meant to be thin: the advice is in the article it links to. Judging
one by its own page called both working sources unusable, which is how a
real candidate nearly got rejected.
"""

import unittest

import check_sources as cs
import expert_extract as ex
import expert_waivers as ew

PLAYERS = {"1": {"full_name": "Jaxson Dart", "position": "QB", "team": "NYG"},
           "2": {"full_name": "Ashton Jeanty", "position": "RB", "team": "LV"},
           "3": {"full_name": "DJ Moore", "position": "WR", "team": "BUF"}}

HUB = ("<html><body>"
       "<a href='/nfl/waiver-wire-week-2-pickups'>Week 2 Waiver Wire Pickups</a>"
       "<a href='/nfl/rankings'>Rankings</a></body></html>")

ARTICLE = ("Week 2 Waiver Wire. Add Jaxson Dart, who takes over as the "
           "starter and is worth 18% of your FAAB budget. Ashton Jeanty is "
           "the top add at running back; spend 25% after the injury ahead of "
           "him. DJ Moore should be added in deeper formats, worth about 6%.")

TEASER = "<html><body>Subscribe to read this article.</body></html>"


class Following(unittest.TestCase):

    def setUp(self):
        self.real = (ew.fetch_raw, ew.fetch_url)
        self.addCleanup(lambda: setattr(ew, "fetch_raw", self.real[0]))
        self.addCleanup(lambda: setattr(ew, "fetch_url", self.real[1]))
        self.gazetteer = ex.build_gazetteer(PLAYERS, list(PLAYERS))

    def serve(self, pages):
        def raw(url, quiet=False):
            for fragment, body in pages.items():
                if fragment in url:
                    return body
            return pages.get("*", "")
        ew.fetch_raw = raw
        ew.fetch_url = lambda url: ew.strip_html(raw(url))

    def test_a_hub_is_judged_by_what_it_links_to(self):
        self.serve({"waiver-wire-week-2": f"<html><body>{ARTICLE}</body></html>",
                    "*": HUB})
        self.assertTrue(cs.check("https://example.com/fantasy/football/",
                                 PLAYERS, self.gazetteer))

    def test_a_hub_whose_articles_are_empty_is_unusable(self):
        self.serve({"waiver-wire-week-2": TEASER, "*": HUB})
        self.assertFalse(cs.check("https://example.com/fantasy/football/",
                                  PLAYERS, self.gazetteer))

    def test_a_hub_with_no_waiver_links_is_unusable(self):
        self.serve({"*": "<html><body><a href='/nfl/rankings'>Rankings</a>"
                         "</body></html>"})
        self.assertFalse(cs.check("https://example.com/fantasy/football/",
                                  PLAYERS, self.gazetteer))

    def test_a_followed_article_does_not_follow_its_own_links(self):
        # Otherwise a thin article walks the whole site.
        self.serve({"*": HUB})
        self.assertFalse(cs.check("https://example.com/anything", PLAYERS,
                                  self.gazetteer, followed=True))

    def test_a_page_that_stands_alone_needs_no_following(self):
        self.serve({"*": f"<html><body>{ARTICLE * 6}</body></html>"})
        self.assertTrue(cs.check("https://example.com/article", PLAYERS,
                                 self.gazetteer))


if __name__ == "__main__":
    unittest.main(verbosity=2)
