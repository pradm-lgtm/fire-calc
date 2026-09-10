#!/usr/bin/env python3
"""
Pre-flight check for candidate article sources.

Before wiring a site into the weekly job, answer three questions about it:
does it fetch at all, does it carry readable article text, and does that text
actually yield player recommendations? A page can pass the first two and
still be useless — a paywall teaser fetches fine and parses to nothing.

    python3 check_sources.py <url> [<url> ...]

Prefer STABLE section URLs (a site's waiver-wire hub) over per-week article
URLs: the scheduled job needs a link that is still right next Tuesday.
"""

import sys

import expert_extract as ex
import expert_waivers as ew
import sleeper_client as sc
import waiver_analyzer as wa


def check(url, players, gazetteer):
    print(f"\n{'=' * 70}\n{url}\n{'=' * 70}")
    text = ew.fetch_url(url)
    if not text:
        print("  FETCH FAILED — unusable as an automated source.")
        print("  If it loads in your browser, the site is blocking scripts;")
        print("  fall back to copying the text (--stdin).")
        return False

    words = len(text.split())
    print(f"  fetched OK — {len(text):,} chars, ~{words:,} words")
    print(f"  starts: {' '.join(text.split())[:100]!r}")

    recs = ex.extract_recommendations(text, gazetteer, source=url,
                                      players=players)
    with_faab = {k: v for k, v in recs.items() if v.get("faab") is not None}
    print(f"  parsed {len(recs)} recommendation(s), "
          f"{len(with_faab)} with a FAAB figure")

    if not recs:
        print("  VERDICT: unusable — no recommendations parsed.")
        print("  Likely a paywall teaser, a link hub, or a table-only page.")
        return False

    for pid, info in list(recs.items())[:8]:
        faab = f"{info['faab']}%" if info["faab"] is not None else "n/a"
        print(f"    - {sc.player_label(players, pid):<34} FAAB {faab}")
    if len(recs) > 8:
        print(f"    ... and {len(recs) - 8} more")

    if words < 400:
        print("  VERDICT: marginal — very little text; likely a teaser.")
    elif len(recs) < 4:
        print("  VERDICT: marginal — few recommendations found.")
    else:
        print("  VERDICT: usable.")
    return True


def main():
    urls = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not urls:
        print(__doc__.strip())
        return 1
    print("Loading player database...")
    try:
        players = sc.all_players()
    except sc.SleeperError as e:
        print(f"ERROR: {e}")
        return 1
    gazetteer = ex.build_gazetteer(
        players, [pid for pid, p in players.items() if wa.is_rosterable(p)]
    )
    print(f"{len(gazetteer):,} matchable player names.")

    usable = sum(1 for u in urls if check(u, players, gazetteer))
    print(f"\n{'=' * 70}")
    print(f"{usable} of {len(urls)} source(s) usable for the weekly job.")
    print("=" * 70)
    return 0 if usable else 1


if __name__ == "__main__":
    sys.exit(main())
