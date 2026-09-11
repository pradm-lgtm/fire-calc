"""
Vercel entry point.

Vercel's Python runtime wants a class named `handler` that behaves like a
BaseHTTPRequestHandler, which is exactly what the page already is, so this
only has to make the project importable and hand it over. Every route is
rewritten here by vercel.json, so one function serves the whole app.

There is no filesystem to keep a database in, so DATABASE_URL must point at
Postgres; store.py picks the backend from that.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webapp import Handler as _Handler  # noqa: E402


class handler(_Handler):
    # Ignored when DATABASE_URL is set, but keeps the attribute defined.
    db_path = os.environ.get("FANTASY_DB", ":memory:")

    def log_message(self, fmt, *args):
        # Vercel captures stdout as function logs; keep request noise out.
        pass
