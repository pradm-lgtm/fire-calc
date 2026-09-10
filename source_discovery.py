#!/usr/bin/env python3
"""
Find this week's waiver article from a stable hub or feed.

Analyst sites keep their actual advice in per-week articles whose URLs change
every Tuesday, while their stable pages (section hubs, RSS feeds) carry only
listings. A job that runs on its own therefore cannot hold fixed article
URLs — it has to start somewhere stable and follow through to whatever is
current.

    python3 source_discovery.py                 # use sources.json
    python3 source_discovery.py <hub-url> ...   # ad-hoc

Link parsing covers both RSS/Atom feeds and ordinary HTML, because sites
offer one or the other and it is not worth caring which.
"""

import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

CONFIG = Path(__file__).resolve().parent / "sources.json"

# A link is a waiver article if it says so. Kept deliberately narrow: hubs are
# full of links to rankings, projections and podcasts.
WAIVER_WORDS = ("waiver", "pickup", "pick-up", "add-drop", "adds-drops",
                "free-agent", "free agent", "faab", "streamers")

# Sections that look like waiver content but are the wrong sport or scope.
EXCLUDE_WORDS = ("baseball", "mlb", "nba", "basketball", "hockey", "nhl",
                 "college", "cfb", "soccer", "golf", "nascar", "wnba",
                 "dynasty-rookie", "best-ball")

_HREF = re.compile(r'href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', re.I | re.S)
_RSS_ITEM = re.compile(r"<item[^>]*>(.*?)</item>", re.I | re.S)
_ATOM_ENTRY = re.compile(r"<entry[^>]*>(.*?)</entry>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_WEEK = re.compile(r"week[\s\-_]?(\d{1,2})", re.I)

# Article URLs carry a date segment or a long story id; hubs and section
# pages do not. This is what stops a hub linking to itself from ranking as
# though it were the week's article.
_ARTICLE_MARK = re.compile(r"/20\d{2}/|[-/]\d{6,}(?:\.html?)?$|/\d{6,}/")
_SECTION_PATH = ("/tag/", "/category/", "/topic/", "/advice/", "/hub",
                 "/index", "/archive")


def _clean(text):
    return " ".join(_TAG.sub(" ", text or "").split())


def _field(block, tag):
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, re.I | re.S)
    if m:
        return _clean(m.group(1))
    m = re.search(rf'<{tag}[^>]*href=["\']([^"\']+)["\']', block, re.I)
    return m.group(1) if m else ""


def parse_links(body, base_url):
    """[(url, title)] from an RSS/Atom feed or an HTML page."""
    out = []
    blocks = _RSS_ITEM.findall(body) or _ATOM_ENTRY.findall(body)
    if blocks:
        for block in blocks:
            link = _field(block, "link") or _field(block, "id")
            title = _field(block, "title")
            if link:
                out.append((urljoin(base_url, link), title))
        return out
    for href, inner in _HREF.findall(body):
        out.append((urljoin(base_url, href), _clean(inner)))
    return out


def looks_like_waiver_article(url, title):
    haystack = f"{url} {title}".lower()
    if not any(w in haystack for w in WAIVER_WORDS):
        return False
    if any(w in haystack for w in EXCLUDE_WORDS):
        return False
    # Hub and index pages list articles; they are not articles.
    path = urlparse(url).path.rstrip("/")
    if not path or path.count("/") < 2:
        return False
    return True


def score(url, title, current_week=None):
    """Higher is more likely to be the article we want this week."""
    haystack = f"{url} {title}".lower()
    path = urlparse(url).path.lower()
    points = 0.0
    if _ARTICLE_MARK.search(path):
        points += 2
    if any(seg in path for seg in _SECTION_PATH):
        points -= 4
    if "waiver" in haystack:
        points += 3
    if any(w in haystack for w in ("pickup", "pick-up", "add", "faab", "fab")):
        points += 1
    weeks = [int(w) for w in _WEEK.findall(haystack) if 0 < int(w) <= 18]
    if weeks:
        points += 2
        if current_week and max(weeks) == current_week:
            points += 5          # this week's article beats last week's
        elif current_week:
            points -= abs(max(weeks) - current_week) * 0.5
    elif current_week:
        points -= 1              # undated pieces are usually evergreen filler
    if title:
        points += 0.5
    return points


def rank_candidates(links, current_week=None, limit=3, hub_url=None):
    hub = (hub_url or "").split("?")[0].rstrip("/")
    seen, scored = set(), []
    for url, title in links:
        clean = url.split("?")[0]
        if clean.rstrip("/") == hub:
            continue  # a hub linking to itself is not this week's article
        if clean in seen or not looks_like_waiver_article(clean, title):
            continue
        seen.add(clean)
        scored.append((score(clean, title, current_week), clean, title))
    scored.sort(key=lambda r: (-r[0], r[1]))
    return scored[:limit]


def load_config():
    if CONFIG.exists():
        return json.loads(CONFIG.read_text())
    return []


def discover(hub_url, current_week=None, limit=2, fetch=None):
    """Resolve one hub/feed URL to its most relevant waiver article URLs."""
    if fetch is None:
        import expert_waivers as ew
        fetch = ew.fetch_raw
    body = fetch(hub_url)
    if not body:
        return []
    return rank_candidates(parse_links(body, hub_url), current_week, limit,
                           hub_url=hub_url)


def main():
    import expert_waivers as ew
    import sleeper_client as sc

    hubs = [a for a in sys.argv[1:] if a.startswith("http")]
    config = []
    if hubs:
        config = [{"name": urlparse(h).netloc, "hub": h} for h in hubs]
    else:
        config = load_config()
    if not config:
        print(f"No hubs given and no {CONFIG.name} found.")
        print("Usage: python3 source_discovery.py <hub-or-feed-url> ...")
        return 1

    week = None
    try:
        week = (sc.current_state() or {}).get("week")
        print(f"Current NFL week: {week}")
    except Exception:
        print("Could not read the current week; ranking without it.")

    found_any = False
    for entry in config:
        hub = entry["hub"]
        print(f"\n{'=' * 70}\n{entry.get('name', hub)}\n  hub: {hub}\n{'=' * 70}")
        hits = discover(hub, week, entry.get("limit", 2), fetch=ew.fetch_raw)
        if not hits:
            print("  no waiver articles found (hub unreachable, or its links")
            print("  are rendered by JavaScript and absent from the HTML)")
            continue
        found_any = True
        for points, url, title in hits:
            print(f"  [{points:5.1f}] {title[:70] or '(no title)'}")
            print(f"          {url}")

    if found_any:
        print("\nFeed these URLs to expert_waivers.py with --url, or let the")
        print("weekly job call discover() and do it for you.")
    return 0 if found_any else 1


if __name__ == "__main__":
    sys.exit(main())
