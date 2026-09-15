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
    bid_low         INTEGER,
    bid_high        INTEGER,
    drop_options    TEXT,
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
    projection   REAL,
    overall_text TEXT,
    locked       INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_lineup_flags_check ON lineup_flags(check_id);

-- A decision you have already made. Keyed by the slot rather than by the
-- check, because every refresh writes new rows: keying it to a check would
-- resurrect every dismissal the moment the page looked again, which is the
-- whole thing this is meant to stop.
CREATE TABLE IF NOT EXISTS lineup_done (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    season     TEXT,
    week       INTEGER,
    league_id  TEXT NOT NULL,
    slot       TEXT,
    player_id  TEXT
);

CREATE INDEX IF NOT EXISTS idx_lineup_done_week ON lineup_done(season, week);

-- Trade offers, worked out on a schedule rather than while you wait. The
-- offers are stored already written out, so the page needs no player
-- database and no value list to render them.
CREATE TABLE IF NOT EXISTS trade_runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    season     TEXT,
    week       INTEGER,
    source     TEXT
);

CREATE TABLE IF NOT EXISTS trade_leagues (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES trade_runs(id),
    league_id   TEXT NOT NULL,
    league_name TEXT,
    summary     TEXT,
    settings    TEXT,
    offers      TEXT
);

CREATE INDEX IF NOT EXISTS idx_trade_leagues_run ON trade_leagues(run_id);

-- "Place them now", pressed on the page. The page runs on a server with no
-- browser and no Sleeper session, so it cannot submit anything itself; it
-- records that you asked, and the Mac that does have a browser picks it up.
CREATE TABLE IF NOT EXISTS submit_requests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    asked_at   TEXT NOT NULL,
    claimed_at TEXT,
    finished_at TEXT,
    detail     TEXT
);

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
    "ALTER TABLE lineup_flags ADD COLUMN overall_text TEXT",
    "ALTER TABLE lineup_flags ADD COLUMN locked INTEGER DEFAULT 0",
    "ALTER TABLE proposals ADD COLUMN bid_low INTEGER",
    "ALTER TABLE proposals ADD COLUMN bid_high INTEGER",
    "ALTER TABLE proposals ADD COLUMN drop_options TEXT",
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
    key = conn.kind, str(path or DB_PATH)
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
        bid_low=f.get("bid_low"), bid_high=f.get("bid_high"),
        drop_options=json.dumps(f.get("drop_options") or []),
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
        overall_text=f.get("overall_text"), locked=int(f.get("locked") or 0),
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


# What a slot said the last time its game had not yet started. Carried over
# rather than recomputed, because a call closes at kickoff: a starter who
# picks up an injury in the first quarter reads as a fresh disagreement with
# consensus, and it is a decision nobody can act on any more.
FROZEN_FIELDS = ("verdict", "detail", "better_name", "rank_text",
                 "overall_text")


def verdicts_before_kickoff(conn, season, week):
    """{(league, slot, player): row} as of the last check taken before the
    game began."""
    rows = conn.execute(
        "SELECT f.* FROM lineup_flags f JOIN lineup_checks c"
        " ON f.check_id = c.id"
        " WHERE c.season = ? AND c.week = ? AND f.locked = 0"
        " ORDER BY f.check_id", (str(season), week)).fetchall()
    return {(r["league_id"], r["slot"], r["player_id"]): r for r in rows}


def write_lineup_check(conn, season, week, sources, rows):
    """Store one check, keeping what a locked slot said before kickoff."""
    previous = verdicts_before_kickoff(conn, season, week)
    for row in rows:
        if not row.get("locked"):
            continue
        was = previous.get((str(row["league_id"]), row.get("slot"),
                            str(row["player_id"]) if row.get("player_id")
                            else None))
        if was:
            row.update({field: was[field] for field in FROZEN_FIELDS})
    check_id = start_lineup_check(conn, season, week, sources)
    for row in rows:
        add_lineup_flag(conn, check_id, **row)
    return check_id


def write_trade_run(conn, season, week, source, leagues):
    """Store one run's offers. The newest run is the only one shown."""
    cur = conn.execute(
        "INSERT INTO trade_runs (created_at, season, week, source)"
        " VALUES (?,?,?,?)", (now(), str(season), week, source))
    run_id = cur.lastrowid
    for league in leagues:
        conn.execute(
            "INSERT INTO trade_leagues (run_id, league_id, league_name,"
            " summary, settings, offers) VALUES (?,?,?,?,?,?)",
            (run_id, str(league["league_id"]), league.get("league_name"),
             league.get("summary", ""),
             json.dumps(league.get("settings") or {}),
             json.dumps(league.get("offers") or [])))
    conn.commit()
    return run_id


def latest_trade_run(conn):
    return conn.execute(
        "SELECT * FROM trade_runs ORDER BY id DESC LIMIT 1").fetchone()


def trade_leagues(conn, run_id):
    rows = conn.execute(
        "SELECT * FROM trade_leagues WHERE run_id = ? ORDER BY id",
        (run_id,)).fetchall()
    return [{"league_id": r["league_id"], "league_name": r["league_name"],
             "summary": r["summary"],
             "settings": json.loads(r["settings"] or "{}"),
             "offers": json.loads(r["offers"] or "[]")} for r in rows]


def ask_to_submit(conn):
    """Record that you pressed the button. Returns the request id."""
    cur = conn.execute(
        "INSERT INTO submit_requests (asked_at) VALUES (?)", (now(),))
    conn.commit()
    return cur.lastrowid


def pending_submit_request(conn):
    """The oldest request nobody has picked up yet."""
    return conn.execute(
        "SELECT * FROM submit_requests WHERE claimed_at IS NULL"
        " ORDER BY id LIMIT 1").fetchone()


def claim_submit_request(conn, request_id):
    """Take a request, so two runs cannot both act on one press."""
    cur = conn.execute(
        "UPDATE submit_requests SET claimed_at = ?"
        " WHERE id = ? AND claimed_at IS NULL", (now(), request_id))
    conn.commit()
    return cur


def finish_submit_request(conn, request_id, detail=""):
    conn.execute(
        "UPDATE submit_requests SET finished_at = ?, detail = ? WHERE id = ?",
        (now(), str(detail)[:500], request_id))
    conn.commit()


def last_submit_request(conn):
    return conn.execute(
        "SELECT * FROM submit_requests ORDER BY id DESC LIMIT 1").fetchone()


def settle(conn, season, week, league_id, slot, player_id):
    """Record that a lineup call has been dealt with."""
    conn.execute(
        "INSERT INTO lineup_done (at, season, week, league_id, slot, player_id)"
        " VALUES (?,?,?,?,?,?)",
        (now(), str(season), week, str(league_id), slot,
         str(player_id) if player_id else None),
    )
    conn.commit()


def unsettle(conn, season, week, league_id, slot, player_id):
    conn.execute(
        "DELETE FROM lineup_done WHERE season = ? AND week = ?"
        " AND league_id = ? AND slot = ? AND player_id = ?",
        (str(season), week, str(league_id), slot,
         str(player_id) if player_id else None),
    )
    conn.commit()


def settled(conn, season, week):
    """{(league_id, slot, player_id)} already dealt with this week."""
    rows = conn.execute(
        "SELECT league_id, slot, player_id FROM lineup_done"
        " WHERE season = ? AND week = ?", (str(season), week)).fetchall()
    return {(r["league_id"], r["slot"], r["player_id"]) for r in rows}


def latest_run(conn):
    row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return row


def proposals_for_run(conn, run_id):
    """A run's proposals, dearest first.

    What the analysts would spend is the closest thing to a ranking of how
    much each one matters, and it decides which to approve when the budget
    will not cover them all.
    """
    rows = conn.execute(
        "SELECT * FROM proposals WHERE run_id = ?", (run_id,)).fetchall()
    return sorted(rows, key=lambda r: (r["league_name"] or "",
                                       -(r["bid"] or 0), r["rank"] or 0,
                                       r["id"]))


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
