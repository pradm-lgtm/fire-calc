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
import uuid
from datetime import datetime, timezone
from pathlib import Path

import db

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

-- Start/sit lives apart from proposals on purpose. A proposal is something
-- to submit; a lineup flag is something to look at, and putting them in one
-- table would be one typo away from the submitter treating "bench Dobbins"
-- as a waiver claim.
CREATE TABLE IF NOT EXISTS lineup_checks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    season     TEXT,
    week       INTEGER,
    sources    TEXT
);

CREATE TABLE IF NOT EXISTS lineup_flags (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    check_id     INTEGER NOT NULL REFERENCES lineup_checks(id),
    league_id    TEXT NOT NULL,
    league_name  TEXT,
    slot         TEXT,
    position     INTEGER DEFAULT 0,
    verdict      TEXT NOT NULL,
    player_id    TEXT,
    player_name  TEXT,
    rank_text    TEXT,
    better_name  TEXT,
    detail       TEXT,
    role         TEXT DEFAULT 'starter',
    pos          TEXT,
    matchup      TEXT,
    projection   REAL
);

CREATE INDEX IF NOT EXISTS idx_lineup_flags_check ON lineup_flags(check_id);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    proposal_id INTEGER REFERENCES proposals(id),
    kind        TEXT NOT NULL,
    detail      TEXT
);
"""


# Columns added after the table already existed somewhere. CREATE TABLE IF
# NOT EXISTS will not add a column to a table that is already there, and the
# two backends spell a conditional ALTER differently, so each one is tried
# and a complaint that it is already present is the expected answer.
MIGRATIONS = [
    "ALTER TABLE lineup_flags ADD COLUMN role TEXT",
    "ALTER TABLE lineup_flags ADD COLUMN pos TEXT",
    "ALTER TABLE lineup_flags ADD COLUMN matchup TEXT",
    "ALTER TABLE lineup_flags ADD COLUMN projection REAL",
]


def apply_migrations(conn):
    for statement in MIGRATIONS:
        try:
            conn.execute(statement)
            conn.commit()
        except Exception:
            # Already applied. Postgres aborts the transaction on a failed
            # statement, so it has to be cleared before the next one.
            try:
                conn.rollback()
            except Exception:
                pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_schema_ready = set()


def connect(path=None):
    """SQLite when run from a directory, Postgres when DATABASE_URL is set.

    The schema is applied once per process rather than per connection. On a
    serverless host the same warm instance serves many requests, and running
    a dozen CREATE TABLE IF NOT EXISTS statements before each one is pure
    latency against a database that is a network hop away.
    """
    conn = db.Connection(path or DB_PATH)
    key = db.backend(), str(path or DB_PATH)
    # Every connection to ":memory:" is a different, empty database, so
    # remembering that one of them has the schema says nothing about the
    # next.
    if str(path) == ":memory:" or key not in _schema_ready:
        conn.executescript(SCHEMA)
        apply_migrations(conn)
        _schema_ready.add(key)
    return conn


def ensure_schema(path=None):
    """Apply the schema regardless, for a fresh database."""
    conn = db.Connection(path or DB_PATH)
    conn.executescript(SCHEMA)
    conn.close()
    _schema_ready.add((db.backend(), str(path or DB_PATH)))


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
    except db.IntegrityError:
        return None
    log(conn, "proposed", f"{cols['add_player_name']} in "
        f"{cols['league_name']}", cur.lastrowid)
    conn.commit()
    return cur.lastrowid


def start_lineup_check(conn, season, week, sources):
    cur = conn.execute(
        "INSERT INTO lineup_checks (created_at, season, week, sources)"
        " VALUES (?,?,?,?)",
        (now(), str(season), week, json.dumps(sources)),
    )
    conn.commit()
    return cur.lastrowid


def add_lineup_flag(conn, check_id, **f):
    cols = dict(
        check_id=check_id, league_id=str(f["league_id"]),
        league_name=f.get("league_name"), slot=f.get("slot"),
        position=f.get("position", 0), verdict=f["verdict"],
        player_id=(str(f["player_id"]) if f.get("player_id") else None),
        player_name=f.get("player_name"), rank_text=f.get("rank_text"),
        better_name=f.get("better_name"), detail=f.get("detail", ""),
        role=f.get("role", "starter"), pos=f.get("pos"),
        matchup=f.get("matchup"), projection=f.get("projection"),
    )
    names = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    cur = conn.execute(
        f"INSERT INTO lineup_flags ({names}) VALUES ({marks})",
        tuple(cols.values()),
    )
    conn.commit()
    return cur.lastrowid


def latest_lineup_check(conn):
    return conn.execute(
        "SELECT * FROM lineup_checks ORDER BY id DESC LIMIT 1").fetchone()


def lineup_flags(conn, check_id):
    return conn.execute(
        "SELECT * FROM lineup_flags WHERE check_id = ?"
        " ORDER BY league_name, position, id", (check_id,)
    ).fetchall()


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


def reset_failed(conn):
    """Return failed rows to 'approved' so they can be attempted again.

    Only rows that failed are eligible: a row recorded as SUBMITTED really
    reached the league and must never be resurrected, or the same claim could
    be placed twice.
    """
    rows = conn.execute(
        "SELECT id FROM proposals WHERE status = ?", (FAILED,)).fetchall()
    for r in rows:
        conn.execute(
            "UPDATE proposals SET status = ?, submitted_at = NULL, result = NULL"
            " WHERE id = ?", (APPROVED, r["id"]))
        log(conn, "retry", "returned to the approved queue", r["id"])
    conn.commit()
    return len(rows)


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
