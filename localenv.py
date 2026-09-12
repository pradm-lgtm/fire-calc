#!/usr/bin/env python3
"""Load .env into the environment, for the machine you run this on.

Only the Yahoo script read .env, so every other tool needed the same values
exported from a shell profile as well - two places holding one secret, and
the failure when they disagreed was a 401 that named neither.

A real environment variable always wins. On Vercel and in Actions the
settings there are the truth and there is no .env at all, so this is a
no-op; on the Mac it is where the values live.
"""

import os
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent / ".env"


def parse(text):
    """{key: value} from KEY=VALUE lines, '#' comments, optional quotes."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def load(path=None):
    """Set anything .env defines that is not already set. Returns the keys."""
    path = Path(path) if path else DEFAULT
    try:
        text = path.read_text()
    except OSError:
        return []
    added = []
    for key, value in parse(text).items():
        if key not in os.environ:
            os.environ[key] = value
            added.append(key)
    return added
