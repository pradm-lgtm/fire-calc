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

SQLITE, POSTGRES = "sqlite", "postgres"


def backend():
    return POSTGRES if os.environ.get("DATABASE_URL") else SQLITE


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
            self._raw = psycopg2.connect(url)
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
        for statement in [s for s in sql.split(";") if s.strip()]:
            cur.execute(self._adapt(statement))
        self._raw.commit()

    def commit(self):
        self._raw.commit()

    def close(self):
        self._raw.close()
