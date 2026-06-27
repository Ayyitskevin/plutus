"""PostgreSQL backend — SQL adapter over the shared SQLite-oriented query layer."""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any

from . import config

log = logging.getLogger("plutus.db.postgres")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS galleries (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    source TEXT,
    photo_count INTEGER NOT NULL DEFAULT 0,
    mise_gallery_id INTEGER,
    tenant_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS recommendation_runs (
    id SERIAL PRIMARY KEY,
    gallery_id INTEGER NOT NULL REFERENCES galleries(id),
    engine TEXT NOT NULL DEFAULT 'mock',
    bundle_count INTEGER NOT NULL DEFAULT 0,
    estimated_total_cents INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    tenant_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS callback_deadletter (
    idempotency_key TEXT PRIMARY KEY,
    gallery_id INTEGER NOT NULL,
    run_id INTEGER,
    payload_json TEXT NOT NULL,
    correlation_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_status TEXT,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _normalize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _normalize_row(row: Any) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return {k: _normalize_value(v) for k, v in row.items()}
    return row


def _adapt_sql(sql: str) -> str:
    out = sql
    out = out.replace("datetime('now')", "NOW()")
    out = out.replace(
        "json_extract(r.payload_json, '$.gallery_theme')",
        "(r.payload_json::json->>'gallery_theme')",
    )
    if "?" in out:
        out = out.replace("?", "%s")
    return out


class _Cursor:
    def __init__(self, cur: Any) -> None:
        self._cur = cur
        self.lastrowid: int | None = None
        self.rowcount: int = 0

    def fetchone(self) -> Any:
        return _normalize_row(self._cur.fetchone())

    def fetchall(self) -> list[Any]:
        return [_normalize_row(row) for row in self._cur.fetchall()]


class _Connection:
    def __init__(self, con: Any) -> None:
        self._con = con

    def execute(self, sql: str, params: tuple | list = ()) -> _Cursor:
        adapted = _adapt_sql(sql)
        cur = self._con.cursor()
        cur.execute(adapted, tuple(params))
        wrapper = _Cursor(cur)
        wrapper.rowcount = cur.rowcount
        return wrapper

    def executescript(self, script: str) -> None:
        cur = self._con.cursor()
        for stmt in script.split(";"):
            chunk = stmt.strip()
            if chunk:
                cur.execute(chunk)


@contextmanager
def connection() -> Iterator[_Connection]:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(config.DATABASE_URL, row_factory=dict_row) as raw:
        yield _Connection(raw)
        raw.commit()


def ping() -> bool:
    with connection() as con:
        con.execute("SELECT 1")
    return True


def migrate() -> None:
    with connection() as con:
        con.executescript(_SCHEMA)
        # Lookup index for one-stable-offer-per-gallery idempotency (not UNIQUE —
        # old deployments may hold duplicate galleries).
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_galleries_mise ON galleries(mise_gallery_id)"
        )
    log.info("postgres schema migrated")