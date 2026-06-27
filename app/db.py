"""Persistence for galleries, recommendations, tenants, and orders (SQLite or Postgres)."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from . import config


def _use_postgres() -> bool:
    return config.DB_BACKEND == "postgres"


def _insert_id(con: Any, sql: str, params: tuple) -> int:
    if _use_postgres():
        cur = con.execute(f"{sql.rstrip()} RETURNING id", params)
        row = cur.fetchone()
        if not row:
            raise RuntimeError("insert did not return id")
        return int(row["id"])
    cur = con.execute(sql, params)
    return int(cur.lastrowid)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS galleries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source TEXT,
    photo_count INTEGER NOT NULL DEFAULT 0,
    mise_gallery_id INTEGER,
    tenant_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS recommendation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gallery_id INTEGER NOT NULL REFERENCES galleries(id),
    engine TEXT NOT NULL DEFAULT 'mock',
    bundle_count INTEGER NOT NULL DEFAULT 0,
    estimated_total_cents INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    tenant_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Operational outbox for offer callbacks that exhausted retries / hard-failed
-- auth. NOT business state: keyed by a stable idempotency key, re-deliverable,
-- and disposable (each row's offer is reproducible from its recommendation run).
CREATE TABLE IF NOT EXISTS callback_deadletter (
    idempotency_key TEXT PRIMARY KEY,
    gallery_id INTEGER NOT NULL,
    run_id INTEGER,
    payload_json TEXT NOT NULL,
    correlation_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_status TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _sqlite_connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


@contextmanager
def _sqlite_connection() -> Iterator[sqlite3.Connection]:
    con = _sqlite_connect()
    try:
        yield con
        con.commit()
    finally:
        con.close()


@contextmanager
def connection() -> Iterator[Any]:
    if _use_postgres():
        from . import db_pg

        with db_pg.connection() as con:
            yield con
        return
    with _sqlite_connection() as con:
        yield con


def ping() -> bool:
    if _use_postgres():
        from . import db_pg

        return db_pg.ping()
    with _sqlite_connection() as con:
        con.execute("SELECT 1")
    return True


def backend_name() -> str:
    return config.DB_BACKEND


def _sqlite_migrate() -> None:
    with _sqlite_connection() as con:
        con.executescript(_SCHEMA)
        gallery_cols = {r[1] for r in con.execute("PRAGMA table_info(galleries)")}
        for col, typ in [("mise_gallery_id", "INTEGER"), ("tenant_id", "TEXT")]:
            if col not in gallery_cols:
                con.execute(f"ALTER TABLE galleries ADD COLUMN {col} {typ}")
        run_cols = {r[1] for r in con.execute("PRAGMA table_info(recommendation_runs)")}
        if "tenant_id" not in run_cols:
            con.execute("ALTER TABLE recommendation_runs ADD COLUMN tenant_id TEXT")
        # Lookup index for one-stable-offer-per-gallery idempotency. Not UNIQUE:
        # pre-existing deployments may hold duplicate galleries from the old
        # always-insert behavior, and a unique constraint would fail to migrate.
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_galleries_mise ON galleries(mise_gallery_id)"
        )


def migrate() -> None:
    if _use_postgres():
        from . import db_pg

        db_pg.migrate()
        return
    _sqlite_migrate()


# --- Galleries & runs ---


def insert_gallery(
    *,
    name: str,
    source: str | None,
    photo_count: int,
    mise_gallery_id: int | None = None,
    tenant_id: str | None = None,
) -> int:
    with connection() as con:
        return _insert_id(
            con,
            """INSERT INTO galleries (name, source, photo_count, mise_gallery_id, tenant_id)
               VALUES (?,?,?,?,?)""",
            (name, source, photo_count, mise_gallery_id, tenant_id),
        )


def insert_run(
    *,
    gallery_id: int,
    engine: str,
    bundle_count: int,
    estimated_total_cents: int,
    payload: dict[str, Any],
    tenant_id: str | None = None,
) -> int:
    with connection() as con:
        return _insert_id(
            con,
            """INSERT INTO recommendation_runs
               (gallery_id, engine, bundle_count, estimated_total_cents, payload_json, tenant_id)
               VALUES (?,?,?,?,?,?)""",
            (
                gallery_id,
                engine,
                bundle_count,
                estimated_total_cents,
                json.dumps(payload),
                tenant_id,
            ),
        )


def update_run(
    run_id: int,
    *,
    tenant_id: str | None = None,
    bundle_count: int | None = None,
    estimated_total_cents: int | None = None,
    payload: dict[str, Any] | None = None,
) -> bool:
    fields: list[str] = []
    params: list[Any] = []
    if bundle_count is not None:
        fields.append("bundle_count=?")
        params.append(bundle_count)
    if estimated_total_cents is not None:
        fields.append("estimated_total_cents=?")
        params.append(estimated_total_cents)
    if payload is not None:
        fields.append("payload_json=?")
        params.append(json.dumps(payload))
    if not fields:
        return False
    sql = f"UPDATE recommendation_runs SET {', '.join(fields)} WHERE id=?"
    params.append(run_id)
    if tenant_id:
        sql += " AND tenant_id=?"
        params.append(tenant_id)
    with connection() as con:
        cur = con.execute(sql, tuple(params))
        return cur.rowcount > 0


def get_run(run_id: int, *, tenant_id: str | None = None) -> dict[str, Any] | None:
    with connection() as con:
        if tenant_id:
            row = con.execute(
                "SELECT * FROM recommendation_runs WHERE id=? AND tenant_id=?",
                (run_id, tenant_id),
            ).fetchone()
        else:
            row = con.execute(
                "SELECT * FROM recommendation_runs WHERE id=?", (run_id,)
            ).fetchone()
    if not row:
        return None
    out = dict(row)
    out["payload"] = json.loads(out.pop("payload_json"))
    return out


def get_gallery_name(gallery_id: int) -> str | None:
    with connection() as con:
        row = con.execute(
            "SELECT name FROM galleries WHERE id=?", (gallery_id,)
        ).fetchone()
    return row["name"] if row else None


def get_gallery(gallery_id: int) -> dict[str, Any] | None:
    with connection() as con:
        row = con.execute("SELECT * FROM galleries WHERE id=?", (gallery_id,)).fetchone()
    return dict(row) if row else None


def get_gallery_by_mise_id(
    mise_gallery_id: int, *, tenant_id: str | None = None
) -> dict[str, Any] | None:
    """Earliest gallery for a Mise gallery id — the stable offer anchor.

    Returns the lowest-id match so re-runs converge on a single gallery even if an
    older deployment left duplicates behind from the previous always-insert path.
    """
    with connection() as con:
        if tenant_id is None:
            row = con.execute(
                "SELECT * FROM galleries WHERE mise_gallery_id=? AND tenant_id IS NULL "
                "ORDER BY id ASC LIMIT 1",
                (mise_gallery_id,),
            ).fetchone()
        else:
            row = con.execute(
                "SELECT * FROM galleries WHERE mise_gallery_id=? AND tenant_id=? "
                "ORDER BY id ASC LIMIT 1",
                (mise_gallery_id, tenant_id),
            ).fetchone()
    return dict(row) if row else None


def run_id_for_gallery(gallery_id: int, *, tenant_id: str | None = None) -> int | None:
    """Canonical (earliest) recommendation run id for a gallery, or None.

    Scopes by tenant the same way get_gallery_by_mise_id does, so the studio
    (tenant_id IS NULL) anchor never reaches across to a tenant-scoped run.
    """
    with connection() as con:
        if tenant_id is None:
            row = con.execute(
                "SELECT id FROM recommendation_runs WHERE gallery_id=? AND tenant_id IS NULL "
                "ORDER BY id ASC LIMIT 1",
                (gallery_id,),
            ).fetchone()
        else:
            row = con.execute(
                "SELECT id FROM recommendation_runs WHERE gallery_id=? AND tenant_id=? "
                "ORDER BY id ASC LIMIT 1",
                (gallery_id, tenant_id),
            ).fetchone()
    return int(row["id"]) if row else None


def update_gallery(
    gallery_id: int, *, name: str | None = None, photo_count: int | None = None
) -> None:
    """Refresh mutable gallery metadata when reusing the stable offer anchor."""
    fields: list[str] = []
    params: list[Any] = []
    if name is not None:
        fields.append("name=?")
        params.append(name)
    if photo_count is not None:
        fields.append("photo_count=?")
        params.append(photo_count)
    if not fields:
        return
    params.append(gallery_id)
    with connection() as con:
        con.execute(f"UPDATE galleries SET {', '.join(fields)} WHERE id=?", tuple(params))


def list_runs(*, limit: int = 20, tenant_id: str | None = None) -> list[dict[str, Any]]:
    with connection() as con:
        if tenant_id:
            rows = con.execute(
                """SELECT r.id, r.gallery_id, r.engine, r.bundle_count,
                          r.estimated_total_cents, r.created_at, g.name AS gallery_name,
                          json_extract(r.payload_json, '$.gallery_theme') AS gallery_theme
                   FROM recommendation_runs r
                   JOIN galleries g ON g.id = r.gallery_id
                   WHERE r.tenant_id=?
                   ORDER BY r.id DESC LIMIT ?""",
                (tenant_id, limit),
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT r.id, r.gallery_id, r.engine, r.bundle_count,
                          r.estimated_total_cents, r.created_at, g.name AS gallery_name,
                          json_extract(r.payload_json, '$.gallery_theme') AS gallery_theme
                   FROM recommendation_runs r
                   JOIN galleries g ON g.id = r.gallery_id
                   ORDER BY r.id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


# --- Callback dead-letter outbox (operational; re-deliverable) ---


def upsert_callback_deadletter(
    *,
    idempotency_key: str,
    gallery_id: int,
    run_id: int | None,
    payload: dict[str, Any],
    correlation_id: str | None,
    attempts: int,
    last_status: str | None,
    last_error: str | None,
) -> None:
    """Record a failed callback delivery, keyed by idempotency key (no duplicates)."""
    with connection() as con:
        con.execute("DELETE FROM callback_deadletter WHERE idempotency_key=?", (idempotency_key,))
        con.execute(
            """INSERT INTO callback_deadletter
               (idempotency_key, gallery_id, run_id, payload_json, correlation_id,
                attempts, last_status, last_error)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                idempotency_key,
                gallery_id,
                run_id,
                json.dumps(payload),
                correlation_id,
                attempts,
                last_status,
                last_error,
            ),
        )


def delete_callback_deadletter(idempotency_key: str) -> None:
    with connection() as con:
        con.execute(
            "DELETE FROM callback_deadletter WHERE idempotency_key=?", (idempotency_key,)
        )


def get_callback_deadletter(idempotency_key: str) -> dict[str, Any] | None:
    with connection() as con:
        row = con.execute(
            "SELECT * FROM callback_deadletter WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
    if not row:
        return None
    out = dict(row)
    out["payload"] = json.loads(out["payload_json"])
    return out


def list_callback_deadletter(*, limit: int = 100) -> list[dict[str, Any]]:
    with connection() as con:
        rows = con.execute(
            "SELECT * FROM callback_deadletter ORDER BY created_at ASC, idempotency_key ASC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item["payload_json"])
        out.append(item)
    return out


def count_callback_deadletter() -> int:
    with connection() as con:
        row = con.execute("SELECT COUNT(*) AS n FROM callback_deadletter").fetchone()
    return int(row["n"])
