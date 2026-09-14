#!/usr/bin/env python3
"""Player trade values, for weighing one side of a deal against the other.

A trade calculator turns a pile of players into one number. Reading those
numbers from an API is arithmetic; driving the calculator's web form would
be scraping, and this checks which one is available.

Values are somebody's opinion, formed from trades made in their tool. They
know nothing about your roster - that a receiver is your third is a fact
they cannot see - so they weigh a deal, they do not judge it.

    python3 trade_values.py
"""

import json
import sys
import urllib.error
import urllib.request

TIMEOUT = 20

# Half PPR, one quarterback, redraft: the shape of both leagues here. The
# wrong settings give confidently wrong numbers rather than no numbers.
SOURCES = [
    ("FantasyCalc",
     "https://api.fantasycalc.com/values/current"
     "?isDynasty=false&numQbs=1&ppr=0.5"),
]

_NAME_KEYS = ("name", "fullName", "playerName")
_VALUE_KEYS = ("value", "redraftValue", "tradeValue")


def _get(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (personal fantasy tool; single user)",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.HTTPError, urllib.error.URLError,
            json.JSONDecodeError, TimeoutError, OSError) as exc:
        return {"__error__": f"{type(exc).__name__}: {exc}"}


def _dig(row, keys):
    """Values live either on the row or on a nested player object."""
    for holder in (row, row.get("player") if isinstance(row, dict) else None):
        if not isinstance(holder, dict):
            continue
        for key in keys:
            if holder.get(key) not in (None, ""):
                return holder[key]
    return None


def values_from(rows):
    """{player name: value}, from whatever shape the rows arrive in."""
    out = {}
    for row in rows if isinstance(rows, list) else []:
        name = _dig(row, _NAME_KEYS)
        value = _dig(row, _VALUE_KEYS)
        if isinstance(name, str) and isinstance(value, (int, float)):
            out[name.strip()] = float(value)
    return out


def fetch():
    """({name: value}, source name). Empty if nothing answered."""
    for name, url in SOURCES:
        data = _get(url)
        rows = data if isinstance(data, list) else (
            data.get("players") if isinstance(data, dict) else None)
        values = values_from(rows)
        if len(values) > 50:
            return values, name
    return {}, None


def main():
    for name, url in SOURCES:
        print(f"{name}: {url}")
        data = _get(url)
        if isinstance(data, dict) and data.get("__error__"):
            print(f"  did not answer: {data['__error__']}")
            print("  -> values would have to come from somewhere else")
            continue
        rows = data if isinstance(data, list) else (
            data.get("players") if isinstance(data, dict) else None)
        print(f"  rows: {len(rows) if isinstance(rows, list) else 'not a list'}")
        if isinstance(rows, list) and rows:
            print(f"  fields: {', '.join(sorted(rows[0])[:20])}")
            print(f"  one row: {json.dumps(rows[0])[:400]}")
        values = values_from(rows)
        print(f"  usable values: {len(values)}")
        for player, value in list(values.items())[:6]:
            print(f"    {player:<26} {value:g}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
