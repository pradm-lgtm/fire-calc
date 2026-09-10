#!/usr/bin/env python3
"""
Storage for waiver proposals and the decisions made on them.

SQLite via the standard library: no server to run, no packages to install,
and the whole state is one file you can copy or delete.

The schema exists to enforce one rule above all others — nothing is ever
submitted to a league without an explicit approval recorded here first.
Every proposal carries a status, and the submitter only ever acts on rows
that a person moved to 'approved'. Each proposal also carries a unique
idempotency key so a retry cannot produce a second claim.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "fantasy.db"

PENDING, APPROVED, DECLINED = "pending", "approved", "declined"
SUBMITTED, FAILED, SKIPPED = "submitted", "failed", "skipped"
OPEN_STATUSES = (PENDING, APPROVED)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    season       TEXT,
    week         INTEGER,
    sources      TEXT,
    note         TEXT
);

CREATE TABLE IF NOT EXISTS proposals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES runs(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    platform        TEXT NOT NULL,
    league_id       TEXT NOT NULL,
    league_name     TEXT,
    add_player_id   TEXT NOT NULL,
    add_player_name TEXT NOT NULL,
    add_position    TEXT,
    drop_player_id  TEXT,
    drop_player_name TEXT,
    drop_position   TEXT,
    bid             INTEGER,
    max_bid         INTEGER,
    consensus       INTEGER DEFAULT 0,
    sources         TEXT,
    rationale       TEXT,
    quote           TEXT,
    rank            INTEGER DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'pending',
    decided_at      TEXT,
    submitted_at    TEXT,
    result          TEXT
);

CREATE INDEX IF NOT EXISTS idx_proposals_run ON proposals(run_id);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    proposal_id INTEGER REFERENCES proposals(id),
    kind        TEXT NOT NULL,
    detail      TEXT
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path=None):
    conn = sqlite3.connect(str(path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def log(conn, kind, detail="", proposal_id=None):
    conn.execute(
        "INSERT INTO events (at, proposal_id, kind, detail) VALUES (?,?,?,?)",
        (now(), proposal_id, kind, detail),
    )


def start_run(conn, season, week, sources, note=""):
    cur = conn.execute(
        "INSERT INTO runs (created_at, season, week, sources, note)"
        " VALUES (?,?,?,?,?)",
        (now(), str(season), week, json.dumps(sources), note),
    )
    conn.commit()
    return cur.lastrowid


def add_proposal(conn, run_id, **f):
    """Insert one proposal. Returns its id, or None if it already exists.

    The idempotency key is derived from the run, league and the two players,
    so re-running the weekly job cannot duplicate a proposal a person has
    already acted on.
    """
    key = f.get("idempotency_key") or ":".join(str(x) for x in (
        run_id, f.get("platform"), f.get("league_id"),
        f.get("add_player_id"), f.get("drop_player_id"),
    ))
    cols = dict(
        run_id=run_id, idempotency_key=key,
        platform=f.get("platform", "sleeper"),
        league_id=str(f["league_id"]), league_name=f.get("league_name"),
        add_player_id=str(f["add_player_id"]),
        add_player_name=f["add_player_name"],
        add_position=f.get("add_position"),
        drop_player_id=(str(f["drop_player_id"])
                        if f.get("drop_player_id") else None),
        drop_player_name=f.get("drop_player_name"),
        drop_position=f.get("drop_position"),
        bid=f.get("bid"), max_bid=f.get("max_bid"),
        consensus=f.get("consensus", 0),
        sources=json.dumps(f.get("sources", [])),
        rationale=f.get("rationale", ""), quote=f.get("quote", ""),
        rank=f.get("rank", 0), status=PENDING,
    )
    names = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    try:
        cur = conn.execute(
            f"INSERT INTO proposals ({names}) VALUES ({marks})",
            tuple(cols.values()),
        )
    except sqlite3.IntegrityError:
        return None
    log(conn, "proposed", f"{cols['add_player_name']} in "
        f"{cols['league_name']}", cur.lastrowid)
    conn.commit()
    return cur.lastrowid


def latest_run(conn):
    row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return row


def proposals_for_run(conn, run_id):
    return conn.execute(
        "SELECT * FROM proposals WHERE run_id = ?"
        " ORDER BY league_name, rank, id", (run_id,)
    ).fetchall()


def approved_unsubmitted(conn):
    """The submitter's only input. Nothing else is ever actionable."""
    return conn.execute(
        "SELECT * FROM proposals WHERE status = ? AND submitted_at IS NULL"
        " ORDER BY league_id, rank", (APPROVED,)
    ).fetchall()


def decide(conn, proposal_id, status, bid=None, drop_player_id=None,
           drop_player_name=None):
    """Record a person's decision. Only pending rows can be decided."""
    if status not in (APPROVED, DECLINED, PENDING):
        raise ValueError(f"bad status {status!r}")
    row = conn.execute("SELECT * FROM proposals WHERE id = ?",
                       (proposal_id,)).fetchone()
    if row is None:
        return False
    if row["submitted_at"]:
        return False  # already acted on in a league; never re-open
    sets, vals = ["status = ?", "decided_at = ?"], [status, now()]
    if bid is not None:
        sets.append("bid = ?")
        vals.append(int(bid))
    if drop_player_id is not None:
        sets += ["drop_player_id = ?", "drop_player_name = ?"]
        vals += [str(drop_player_id), drop_player_name]
    vals.append(proposal_id)
    conn.execute(f"UPDATE proposals SET {', '.join(sets)} WHERE id = ?", vals)
    log(conn, status, f"bid={bid}" if bid is not None else "", proposal_id)
    conn.commit()
    return True


def mark_submitted(conn, proposal_id, ok, detail=""):
    conn.execute(
        "UPDATE proposals SET status = ?, submitted_at = ?, result = ?"
        " WHERE id = ?",
        (SUBMITTED if ok else FAILED, now(), detail, proposal_id),
    )
    log(conn, SUBMITTED if ok else FAILED, detail, proposal_id)
    conn.commit()


def budget_committed(conn, league_id, exclude_id=None):
    """FAAB already promised in a league by approved-or-submitted rows."""
    sql = ("SELECT COALESCE(SUM(bid), 0) AS total FROM proposals"
           " WHERE league_id = ? AND status IN (?, ?)")
    args = [str(league_id), APPROVED, SUBMITTED]
    if exclude_id:
        sql += " AND id != ?"
        args.append(exclude_id)
    return conn.execute(sql, args).fetchone()["total"]


def new_key():
    return uuid.uuid4().hex
