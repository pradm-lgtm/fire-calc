#!/usr/bin/env python3
"""
Pre-flight check for candidate article sources.

Before wiring a site into the weekly job, answer three questions about it:
does it fetch at all, does it carry readable article text, and does that text
actually yield player recommendations? A page can pass the first two and
still be useless — a paywall teaser fetches fine and parses to nothing.

    python3 check_sources.py URL [URL ...]
    python3 check_sources.py URL --debug     (show every player named)

Prefer STABLE section URLs (a site's waiver-wire hub) over per-week article
URLs: the scheduled job needs a link that is still right next Tuesday.
"""

import sys

import expert_extract as ex
import expert_waivers as ew
import sleeper_client as sc
import waiver_analyzer as wa


# Video and podcast pages carry player names in an auto-transcript: no
# headings, no "spend 15%", and speech artefacts that mangle names ("Isaiah,
# likely"). They fetch fine and read as articles, so they need naming.
VIDEO_MARKERS = ("now playing", "paused ad", "ad playing", "watch the full",
                 "subscribe on apple", "listen now", "episode", "transcript")
_TIMESTAMP = __import__("re").compile(r"\b\d{1,2}:\d{2}\b")
_SPEECH = __import__("re").compile(
    r"\b(?:uh|um|yeah|gonna|wanna|you know|i mean|let's go to)\b", __import__("re").I)


def looks_like_transcript(text):
    """(is_transcript, why) - video pages masquerading as articles."""
    low = text.lower()
    hits = [m for m in VIDEO_MARKERS if m in low]
    stamps = len(_TIMESTAMP.findall(text))
    speech = len(_SPEECH.findall(text))
    points, reasons = 0, []
    if hits:
        points += len(hits)
        reasons.append(f"player-UI text ({', '.join(hits[:3])})")
    if stamps >= 3:
        points += 1
        reasons.append(f"{stamps} video timestamps")
    if speech >= 5:
        points += 1
        reasons.append(f"{speech} spoken-filler phrases")
    # Any one signal can appear innocently; three is a video page.
    return points >= 3, "; ".join(reasons)


def check(url, players, gazetteer, debug=False):
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

    trace = [] if debug else None
    transcript, why = looks_like_transcript(text)
    if transcript:
        print(f"  LOOKS LIKE A VIDEO/PODCAST PAGE — {why}")

    recs = ex.extract_recommendations(text, gazetteer, source=url,
                                      players=players, trace=trace)
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

    if debug and trace:
        print(f"\n  --- every player named ({len(trace)} mentions) ---")
        for row in trace:
            mark = "KEEP" if row["verdict"] == "accepted" else "skip"
            print(f"  {mark}  {sc.player_label(players, row['player_id']):<32} "
                  f"{row['reason']}")
            print(f"        {row['sentence']!r}")
        print("  --- end ---\n")

    if transcript:
        print("  VERDICT: unusable — this is a video/podcast page, not a")
        print("  written article. Spoken advice has no headings and no stated")
        print("  bids, and transcription mangles names. Find this site's")
        print("  WRITTEN waiver column instead.")
    elif words < 400:
        print("  VERDICT: marginal — very little text; likely a teaser.")
    elif len(recs) < 4:
        print("  VERDICT: marginal — few recommendations found.")
    elif not with_faab:
        print("  VERDICT: names only — no bid guidance parsed. Often means a")
        print("  listing/hub page rather than an analyst article. Useful for")
        print("  finding candidates, but it cannot tell you what to spend.")
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

    debug = "--debug" in sys.argv
    usable = sum(1 for u in urls if check(u, players, gazetteer, debug))
    print(f"\n{'=' * 70}")
    print(f"{usable} of {len(urls)} source(s) usable for the weekly job.")
    print("=" * 70)
    return 0 if usable else 1


if __name__ == "__main__":
    sys.exit(main())
