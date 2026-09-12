#!/usr/bin/env python3
"""
One storage interface over SQLite and Postgres.

The agent ran on a SQLite file next to itself; hosted on a serverless
platform there is no filesystem to keep one in, so the same schema has to
live in Postgres. Rather than fork the storage layer, this papers over the
handful of real differences:

  * placeholders   sqlite3 uses ?, psycopg2 uses %s
  * new row ids    sqlite3 exposes lastrowid, Postgres needs RETURNING id
  * autoincrement  AUTOINCREMENT vs a serial identity column
  * rows           both are made to behave like dicts

Which backend is used depends on DATABASE_URL being set, so local runs stay
on a file and need nothing installed.
"""

import os
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SQLITE, POSTGRES = "sqlite", "postgres"


def backend():
    return POSTGRES if os.environ.get("DATABASE_URL") else SQLITE


# The client library refuses a connection option it does not recognise, and
# a hosted database hands out a URL written for whatever version it runs.
# Neon's carries channel_binding, which older builds of the bundled client do
# not have, and the refusal reads as an ordinary query error.
_UNKNOWN_OPTION = re.compile(r'invalid connection option "([^"]+)"')


def _without(url, option):
    """The same URL with one query parameter removed."""
    parts = urlsplit(url)
    keep = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k != option]
    return urlunsplit(parts._replace(query=urlencode(keep)))


def _connect(psycopg2, url, tried=()):
    """Connect, dropping options this client cannot understand.

    Dropping one is safe: every option here is a refinement of a connection
    that sslmode=require already protects, and the alternative is no
    connection at all.
    """
    try:
        return psycopg2.connect(url)
    except psycopg2.Error as exc:
        found = _UNKNOWN_OPTION.search(str(exc))
        if not found or found.group(1) in tried:
            raise
        option = found.group(1)
        print(f"[db] this client does not support {option}; "
              "connecting without it")
        return _connect(psycopg2, _without(url, option), tuple(tried) + (option,))


def statements(script):
    """The executable statements of a SQL script, comments removed.

    Splitting on ";" alone is not enough. A semicolon inside a comment ends
    a statement that has not ended: the halves come out as a fragment
    Postgres cannot parse and a piece that is nothing but comment, which it
    rejects as an empty query. sqlite3 runs whole scripts itself and never
    saw this, so one prose semicolon broke every hosted deployment while
    every local run stayed green.
    """
    out, buf, i, n, in_string = [], [], 0, len(script), False
    while i < n:
        ch = script[i]
        if in_string:
            buf.append(ch)
            if ch == "'":
                if script.startswith("''", i):   # an escaped quote, not the end
                    buf.append("'")
                    i += 1
                else:
                    in_string = False
        elif ch == "'":
            in_string = True
            buf.append(ch)
        elif script.startswith("--", i):
            end = script.find("\n", i)
            i = n if end < 0 else end
            continue
        elif ch == ";":
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    out.append("".join(buf))
    return [s.strip() for s in out if s.strip()]


class IntegrityError(Exception):
    """Backend-neutral duplicate-key error."""


class _Cursor:
    def __init__(self, cur, rowid=None):
        self._cur = cur
        self.lastrowid = rowid

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self._cur.fetchall())


class Connection:
    """Just enough of sqlite3.Connection for this project's needs."""

    def __init__(self, dsn=None):
        self.kind = backend()
        if self.kind == POSTGRES:
            import psycopg2
            import psycopg2.extras
            self._mod = psycopg2
            url = os.environ["DATABASE_URL"]
            # Hosted Postgres wants TLS, and Supabase refuses without it.
            if "sslmode=" not in url:
                url += ("&" if "?" in url else "?") + "sslmode=require"
            self._raw = _connect(psycopg2, url)
            self._factory = psycopg2.extras.RealDictCursor
        else:
            import sqlite3
            self._mod = sqlite3
            self._raw = sqlite3.connect(str(dsn))
            self._raw.row_factory = sqlite3.Row
            self._raw.execute("PRAGMA foreign_keys = ON")

    # -- statement translation ------------------------------------------
    def _adapt(self, sql):
        if self.kind == SQLITE:
            return sql
        sql = sql.replace("?", "%s")
        # AUTOINCREMENT is SQLite's spelling of an identity column.
        sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT",
                          "SERIAL PRIMARY KEY")
        return sql

    def execute(self, sql, params=()):
        statement = self._adapt(sql)
        rowid = None
        if self.kind == POSTGRES:
            cur = self._raw.cursor(cursor_factory=self._factory)
            wants_id = (re.match(r"\s*INSERT\s", statement, re.I)
                        and "RETURNING" not in statement.upper())
            if wants_id:
                statement = statement.rstrip().rstrip(";") + " RETURNING id"
            try:
                cur.execute(statement, params)
            except self._mod.IntegrityError as exc:
                self._raw.rollback()
                raise IntegrityError(str(exc)) from None
            if wants_id:
                row = cur.fetchone()
                rowid = row["id"] if row else None
            return _Cursor(cur, rowid)

        try:
            cur = self._raw.execute(statement, params)
        except self._mod.IntegrityError as exc:
            raise IntegrityError(str(exc)) from None
        return _Cursor(cur, cur.lastrowid)

    def executescript(self, sql):
        if self.kind == SQLITE:
            self._raw.executescript(sql)
            return
        cur = self._raw.cursor()
        for statement in statements(sql):
            cur.execute(self._adapt(statement))
        self._raw.commit()

    def commit(self):
        self._raw.commit()

    def close(self):
        self._raw.close()
