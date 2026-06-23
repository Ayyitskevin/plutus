"""SQLite persistence for galleries and recommendation runs."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS galleries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source TEXT,
    photo_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS recommendation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gallery_id INTEGER NOT NULL REFERENCES galleries(id),
    engine TEXT NOT NULL DEFAULT 'mock',
    bundle_count INTEGER NOT NULL DEFAULT 0,
    estimated_total_cents INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    con = _connect()
    try:
        yield con
        con.commit()
    finally:
        con.close()


def migrate() -> None:
    with connection() as con:
        con.executescript(_SCHEMA)


def insert_gallery(*, name: str, source: str | None, photo_count: int) -> int:
    with connection() as con:
        cur = con.execute(
            "INSERT INTO galleries (name, source, photo_count) VALUES (?,?,?)",
            (name, source, photo_count),
        )
        return int(cur.lastrowid)


def insert_run(
    *,
    gallery_id: int,
    engine: str,
    bundle_count: int,
    estimated_total_cents: int,
    payload: dict[str, Any],
) -> int:
    with connection() as con:
        cur = con.execute(
            """INSERT INTO recommendation_runs
               (gallery_id, engine, bundle_count, estimated_total_cents, payload_json)
               VALUES (?,?,?,?,?)""",
            (
                gallery_id,
                engine,
                bundle_count,
                estimated_total_cents,
                json.dumps(payload),
            ),
        )
        return int(cur.lastrowid)


def get_run(run_id: int) -> dict[str, Any] | None:
    with connection() as con:
        row = con.execute(
            "SELECT * FROM recommendation_runs WHERE id=?", (run_id,)
        ).fetchone()
    if not row:
        return None
    out = dict(row)
    out["payload"] = json.loads(out.pop("payload_json"))
    return out


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    with connection() as con:
        rows = con.execute(
            """SELECT r.id, r.gallery_id, r.engine, r.bundle_count,
                      r.estimated_total_cents, r.created_at, g.name AS gallery_name
               FROM recommendation_runs r
               JOIN galleries g ON g.id = r.gallery_id
               ORDER BY r.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]