"""Persistence for galleries, recommendations, tenants, and orders (SQLite or Postgres)."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
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

CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    store_slug TEXT UNIQUE,
    active INTEGER NOT NULL DEFAULT 1,
    monthly_recommend_cap INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT,
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    billing_status TEXT,
    plan_tier TEXT DEFAULT 'trial'
);

CREATE TABLE IF NOT EXISTS tenant_api_keys (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    label TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    revoked_at TEXT,
    FOREIGN KEY(tenant_id) REFERENCES tenants(id)
);

CREATE INDEX IF NOT EXISTS idx_tenant_keys_prefix ON tenant_api_keys(key_prefix);

CREATE TABLE IF NOT EXISTS tenant_usage (
    tenant_id TEXT NOT NULL,
    period TEXT NOT NULL,
    recommends INTEGER NOT NULL DEFAULT 0,
    orders INTEGER NOT NULL DEFAULT 0,
    revenue_cents INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (tenant_id, period),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    tenant_id TEXT,
    actor TEXT,
    action TEXT NOT NULL,
    resource TEXT,
    status TEXT,
    detail TEXT,
    ip TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_log(tenant_id, created_at);

CREATE TABLE IF NOT EXISTS stripe_webhook_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    processed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS storefront_tokens (
    token TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    run_id INTEGER NOT NULL,
    label TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id),
    FOREIGN KEY(run_id) REFERENCES recommendation_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_storefront_tenant ON storefront_tokens(tenant_id, run_id);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    run_id INTEGER NOT NULL,
    bundle_index INTEGER NOT NULL DEFAULT 0,
    stripe_session_id TEXT,
    stripe_payment_intent TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    total_cents INTEGER NOT NULL DEFAULT 0,
    client_email TEXT,
    client_name TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    paid_at TEXT,
    FOREIGN KEY(tenant_id) REFERENCES tenants(id),
    FOREIGN KEY(run_id) REFERENCES recommendation_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_orders_tenant ON orders(tenant_id, created_at);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    sku TEXT NOT NULL,
    label TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    unit_cents INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(order_id) REFERENCES orders(id)
);

CREATE TABLE IF NOT EXISTS product_overrides (
    tenant_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    unit_cents INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    label TEXT,
    PRIMARY KEY (tenant_id, sku),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS fulfillment_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(order_id) REFERENCES orders(id)
);

CREATE INDEX IF NOT EXISTS idx_fulfillment_order ON fulfillment_events(order_id, created_at);

CREATE TABLE IF NOT EXISTS upload_batches (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    photo_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open',
    run_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id)
);

CREATE INDEX IF NOT EXISTS idx_upload_batches_tenant ON upload_batches(tenant_id, created_at);
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
        tenant_cols = {r[1] for r in con.execute("PRAGMA table_info(tenants)")}
        if "notify_email" not in tenant_cols:
            con.execute("ALTER TABLE tenants ADD COLUMN notify_email TEXT")
        if "email_verified_at" not in tenant_cols:
            con.execute("ALTER TABLE tenants ADD COLUMN email_verified_at TEXT")
            con.execute(
                "UPDATE tenants SET email_verified_at=created_at "
                "WHERE email_verified_at IS NULL"
            )
        con.execute(
            """CREATE TABLE IF NOT EXISTS signup_verifications (
                token TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                email TEXT NOT NULL,
                key_id TEXT,
                api_key TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at TEXT NOT NULL,
                verified_at TEXT
            )"""
        )
        signup_cols = {r[1] for r in con.execute("PRAGMA table_info(signup_verifications)")}
        if "key_id" not in signup_cols:
            con.execute("ALTER TABLE signup_verifications ADD COLUMN key_id TEXT")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_signup_verify_tenant "
            "ON signup_verifications(tenant_id, created_at)"
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS tenant_invites (
                token TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                email TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at TEXT NOT NULL,
                claimed_at TEXT
            )"""
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_tenant_invites_tenant "
            "ON tenant_invites(tenant_id, created_at)"
        )
        order_cols = {r[1] for r in con.execute("PRAGMA table_info(orders)")}
        for col, typ in [("lab_status", "TEXT"), ("lab_ref", "TEXT"), ("client_token", "TEXT")]:
            if col not in order_cols:
                con.execute(f"ALTER TABLE orders ADD COLUMN {col} {typ}")
        con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_client_token "
            "ON orders(client_token) WHERE client_token IS NOT NULL"
        )
        import secrets

        missing = con.execute(
            "SELECT id FROM orders WHERE client_token IS NULL OR client_token = ''"
        ).fetchall()
        for (oid,) in missing:
            con.execute(
                "UPDATE orders SET client_token=? WHERE id=?",
                (secrets.token_urlsafe(18), oid),
            )
        batch_cols = {r[1] for r in con.execute("PRAGMA table_info(upload_batches)")}
        for col, typ in [
            ("analyze_error", "TEXT"),
            ("argus_run_id", "INTEGER"),
            ("analyze_started_at", "TEXT"),
        ]:
            if col not in batch_cols:
                con.execute(f"ALTER TABLE upload_batches ADD COLUMN {col} {typ}")
        con.execute(
            """CREATE TABLE IF NOT EXISTS ui_sessions (
                id TEXT PRIMARY KEY,
                tenant_id TEXT,
                api_key_id TEXT,
                is_admin INTEGER NOT NULL DEFAULT 0,
                csrf_token TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at TEXT NOT NULL
            )"""
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_upload_batches_status "
            "ON upload_batches(status, created_at)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_tenants_stripe_customer "
            "ON tenants(stripe_customer_id) WHERE stripe_customer_id IS NOT NULL"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_stripe_session "
            "ON orders(stripe_session_id) WHERE stripe_session_id IS NOT NULL"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_lab_poll "
            "ON orders(status, lab_status)"
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


# --- Tenants ---


def _tenant_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    keys = row.keys()
    return {
        "id": row["id"],
        "name": row["name"],
        "store_slug": row["store_slug"] if "store_slug" in keys else None,
        "active": bool(row["active"]),
        "monthly_recommend_cap": row["monthly_recommend_cap"]
        if "monthly_recommend_cap" in keys
        else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"] if "updated_at" in keys else None,
        "stripe_customer_id": row["stripe_customer_id"] if "stripe_customer_id" in keys else None,
        "stripe_subscription_id": row["stripe_subscription_id"]
        if "stripe_subscription_id" in keys
        else None,
        "billing_status": row["billing_status"] if "billing_status" in keys else None,
        "plan_tier": row["plan_tier"] if "plan_tier" in keys else None,
        "notify_email": row["notify_email"] if "notify_email" in keys else None,
        "email_verified_at": row["email_verified_at"] if "email_verified_at" in keys else None,
    }


def create_tenant(
    tenant_id: str,
    *,
    name: str,
    store_slug: str,
    monthly_recommend_cap: int | None = None,
) -> dict:
    with connection() as con:
        con.execute(
            """INSERT INTO tenants (id, name, store_slug, monthly_recommend_cap, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (tenant_id, name, store_slug, monthly_recommend_cap),
        )
    tenant = get_tenant(tenant_id)
    assert tenant is not None
    return tenant


def get_tenant(tenant_id: str) -> dict | None:
    with connection() as con:
        row = con.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone()
    return _tenant_dict(row)


def get_tenant_by_stripe_customer(stripe_customer_id: str) -> dict | None:
    with connection() as con:
        row = con.execute(
            "SELECT * FROM tenants WHERE stripe_customer_id=?",
            (stripe_customer_id,),
        ).fetchone()
    return _tenant_dict(row)


def get_tenant_by_slug(store_slug: str) -> dict | None:
    with connection() as con:
        row = con.execute(
            "SELECT * FROM tenants WHERE store_slug=? AND active=1", (store_slug,)
        ).fetchone()
    return _tenant_dict(row)


def list_tenants(*, active_only: bool = False) -> list[dict]:
    with connection() as con:
        sql = "SELECT * FROM tenants"
        if active_only:
            sql += " WHERE active=1"
        sql += " ORDER BY id"
        return [_tenant_dict(row) for row in con.execute(sql).fetchall()]


def update_tenant(tenant_id: str, **fields) -> dict | None:
    allowed = {
        "name",
        "store_slug",
        "active",
        "monthly_recommend_cap",
        "stripe_customer_id",
        "stripe_subscription_id",
        "billing_status",
        "plan_tier",
        "notify_email",
        "email_verified_at",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return get_tenant(tenant_id)
    if "active" in updates:
        updates["active"] = 1 if updates["active"] else 0
    assignments = ", ".join(f"{key}=?" for key in updates)
    values = list(updates.values()) + [tenant_id]
    with connection() as con:
        con.execute(
            f"UPDATE tenants SET {assignments}, updated_at=datetime('now') WHERE id=?",
            values,
        )
    return get_tenant(tenant_id)


def insert_signup_verification(
    *,
    token: str,
    tenant_id: str,
    email: str,
    key_id: str | None,
    expires_at: str,
) -> None:
    with connection() as con:
        con.execute(
            """INSERT INTO signup_verifications
               (token, tenant_id, email, key_id, api_key, expires_at)
               VALUES (?, ?, ?, ?, '', ?)""",
            (token, tenant_id, email, key_id, expires_at),
        )


def get_signup_verification(token: str) -> dict | None:
    with connection() as con:
        row = con.execute(
            "SELECT * FROM signup_verifications WHERE token=?",
            (token,),
        ).fetchone()
    if not row:
        return None
    return dict(row)


def get_pending_signup_verification_by_email(email: str) -> dict | None:
    with connection() as con:
        row = con.execute(
            """SELECT * FROM signup_verifications
               WHERE email=? AND verified_at IS NULL
               ORDER BY created_at DESC LIMIT 1""",
            (email,),
        ).fetchone()
    if not row:
        return None
    return dict(row)


def mark_signup_verification_verified(token: str, *, verified_at: str) -> None:
    with connection() as con:
        con.execute(
            "UPDATE signup_verifications SET verified_at=? WHERE token=?",
            (verified_at, token),
        )


def insert_tenant_invite(
    *,
    token: str,
    tenant_id: str,
    email: str,
    expires_at: str,
) -> None:
    with connection() as con:
        con.execute(
            """INSERT INTO tenant_invites (token, tenant_id, email, expires_at)
               VALUES (?, ?, ?, ?)""",
            (token, tenant_id, email, expires_at),
        )


def get_tenant_invite(token: str) -> dict | None:
    with connection() as con:
        row = con.execute(
            "SELECT * FROM tenant_invites WHERE token=?",
            (token,),
        ).fetchone()
    if not row:
        return None
    return dict(row)


def get_pending_tenant_invite(tenant_id: str) -> dict | None:
    with connection() as con:
        row = con.execute(
            """SELECT * FROM tenant_invites
               WHERE tenant_id=? AND claimed_at IS NULL
               ORDER BY created_at DESC LIMIT 1""",
            (tenant_id,),
        ).fetchone()
    if not row:
        return None
    invite = dict(row)
    expires = invite.get("expires_at")
    if not expires:
        return invite
    try:
        exp_dt = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=UTC)
        if datetime.now(UTC) > exp_dt:
            return None
    except ValueError:
        return None
    return invite


def revoke_pending_tenant_invites(tenant_id: str) -> None:
    with connection() as con:
        con.execute(
            "DELETE FROM tenant_invites WHERE tenant_id=? AND claimed_at IS NULL",
            (tenant_id,),
        )


def mark_tenant_invite_claimed(token: str, *, claimed_at: str) -> None:
    with connection() as con:
        con.execute(
            "UPDATE tenant_invites SET claimed_at=? WHERE token=?",
            (claimed_at, token),
        )


def insert_tenant_api_key(
    *,
    key_id: str,
    tenant_id: str,
    key_prefix: str,
    key_hash: str,
    label: str | None = None,
) -> None:
    with connection() as con:
        con.execute(
            """INSERT INTO tenant_api_keys (id, tenant_id, key_prefix, key_hash, label)
               VALUES (?, ?, ?, ?, ?)""",
            (key_id, tenant_id, key_prefix, key_hash, label),
        )


def find_tenant_by_key_prefix(key_prefix: str) -> list[dict]:
    with connection() as con:
        rows = con.execute(
            """SELECT k.id AS key_id, k.key_hash, k.revoked_at, k.label,
                      t.id AS tenant_id, t.name, t.store_slug, t.active,
                      t.monthly_recommend_cap, t.created_at, t.updated_at,
                      t.stripe_customer_id, t.stripe_subscription_id,
                      t.billing_status, t.plan_tier, t.notify_email,
                      t.email_verified_at
               FROM tenant_api_keys k
               JOIN tenants t ON t.id = k.tenant_id
               WHERE k.key_prefix=? AND k.revoked_at IS NULL AND t.active=1""",
            (key_prefix,),
        ).fetchall()
    out = []
    for row in rows:
        tenant = {
            "id": row["tenant_id"],
            "name": row["name"],
            "store_slug": row["store_slug"],
            "active": bool(row["active"]),
            "monthly_recommend_cap": row["monthly_recommend_cap"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "stripe_customer_id": row["stripe_customer_id"],
            "stripe_subscription_id": row["stripe_subscription_id"],
            "billing_status": row["billing_status"],
            "plan_tier": row["plan_tier"],
            "notify_email": row["notify_email"] if "notify_email" in row.keys() else None,
            "email_verified_at": row["email_verified_at"]
            if "email_verified_at" in row.keys()
            else None,
        }
        out.append({"key_id": row["key_id"], "key_hash": row["key_hash"], "tenant": tenant})
    return out


def revoke_tenant_api_key(key_id: str) -> bool:
    with connection() as con:
        cur = con.execute(
            """UPDATE tenant_api_keys SET revoked_at=datetime('now')
               WHERE id=? AND revoked_at IS NULL""",
            (key_id,),
        )
        return cur.rowcount > 0


def list_tenant_keys(tenant_id: str) -> list[dict]:
    with connection() as con:
        rows = con.execute(
            """SELECT id, tenant_id, key_prefix, label, created_at, revoked_at
               FROM tenant_api_keys WHERE tenant_id=? ORDER BY created_at DESC""",
            (tenant_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_tenant_api_key(key_id: str) -> dict | None:
    with connection() as con:
        row = con.execute(
            """SELECT id, tenant_id, key_prefix, label, created_at, revoked_at
               FROM tenant_api_keys WHERE id=?""",
            (key_id,),
        ).fetchone()
    return dict(row) if row else None


def _usage_period(dt: datetime | None = None) -> str:
    dt = dt or datetime.now()
    return dt.strftime("%Y-%m")


def get_tenant_usage(tenant_id: str, period: str | None = None) -> dict:
    period = period or _usage_period()
    with connection() as con:
        row = con.execute(
            "SELECT * FROM tenant_usage WHERE tenant_id=? AND period=?",
            (tenant_id, period),
        ).fetchone()
    if row is None:
        return {
            "tenant_id": tenant_id,
            "period": period,
            "recommends": 0,
            "orders": 0,
            "revenue_cents": 0,
        }
    return {
        "tenant_id": tenant_id,
        "period": period,
        "recommends": int(row["recommends"]),
        "orders": int(row["orders"]),
        "revenue_cents": int(row["revenue_cents"]),
        "updated_at": row["updated_at"],
    }


def increment_tenant_usage(
    tenant_id: str,
    *,
    recommends: int = 0,
    orders: int = 0,
    revenue_cents: int = 0,
    period: str | None = None,
) -> dict:
    period = period or _usage_period()
    with connection() as con:
        con.execute(
            """INSERT INTO tenant_usage
               (tenant_id, period, recommends, orders, revenue_cents, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(tenant_id, period) DO UPDATE SET
                 recommends = tenant_usage.recommends + excluded.recommends,
                 orders = tenant_usage.orders + excluded.orders,
                 revenue_cents = tenant_usage.revenue_cents + excluded.revenue_cents,
                 updated_at = datetime('now')""",
            (tenant_id, period, recommends, orders, revenue_cents),
        )
    return get_tenant_usage(tenant_id, period)


def try_increment_recommend_under_cap(tenant_id: str, cap: int) -> bool:
    """Atomically increment recommends if under monthly cap."""
    if cap <= 0:
        increment_tenant_usage(tenant_id, recommends=1)
        return True
    period = _usage_period()
    with connection() as con:
        con.execute(
            """INSERT INTO tenant_usage
               (tenant_id, period, recommends, orders, revenue_cents, updated_at)
               VALUES (?, ?, 0, 0, 0, datetime('now'))
               ON CONFLICT(tenant_id, period) DO NOTHING""",
            (tenant_id, period),
        )
        cur = con.execute(
            """UPDATE tenant_usage SET recommends = recommends + 1,
                      updated_at = datetime('now')
               WHERE tenant_id = ? AND period = ? AND recommends < ?""",
            (tenant_id, period, cap),
        )
        return cur.rowcount > 0


def global_usage_totals() -> dict:
    period = _usage_period()
    with connection() as con:
        row = con.execute(
            """SELECT COALESCE(SUM(recommends),0) AS recommends,
                      COALESCE(SUM(orders),0) AS orders,
                      COALESCE(SUM(revenue_cents),0) AS revenue_cents
               FROM tenant_usage WHERE period=?""",
            (period,),
        ).fetchone()
    return {
        "period": period,
        "recommends": int(row["recommends"]),
        "orders": int(row["orders"]),
        "revenue_cents": int(row["revenue_cents"]),
    }


# --- Audit ---


def insert_audit_event(
    *,
    action: str,
    tenant_id: str | None = None,
    actor: str | None = None,
    resource: str | None = None,
    status: str = "ok",
    detail: dict[str, Any] | str | None = None,
    ip: str | None = None,
) -> None:
    detail_json = json.dumps(detail) if isinstance(detail, dict) else detail
    with connection() as con:
        con.execute(
            """INSERT INTO audit_log (tenant_id, actor, action, resource, status, detail, ip)
               VALUES (?,?,?,?,?,?,?)""",
            (tenant_id, actor, action, resource, status, detail_json, ip),
        )


def list_audit_events(
    *,
    tenant_id: str | None = None,
    action: str | None = None,
    limit: int = 50,
) -> list[dict]:
    clauses = []
    params: list[Any] = []
    if tenant_id:
        clauses.append("tenant_id=?")
        params.append(tenant_id)
    if action:
        clauses.append("action=?")
        params.append(action)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with connection() as con:
        rows = con.execute(
            f"SELECT * FROM audit_log {where} ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        if item.get("detail"):
            try:
                item["detail"] = json.loads(item["detail"])
            except (json.JSONDecodeError, TypeError):
                pass
        out.append(item)
    return out


def purge_audit_events(*, older_than_days: int) -> int:
    cutoff = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()
    with connection() as con:
        cur = con.execute("DELETE FROM audit_log WHERE created_at < ?", (cutoff,))
        return cur.rowcount


# --- Stripe webhook dedup ---


def is_stripe_webhook_processed(event_id: str) -> bool:
    with connection() as con:
        row = con.execute(
            "SELECT 1 FROM stripe_webhook_events WHERE event_id=?",
            (event_id,),
        ).fetchone()
    return row is not None


def record_stripe_webhook_event(event_id: str, event_type: str) -> bool:
    with connection() as con:
        try:
            con.execute(
                "INSERT INTO stripe_webhook_events (event_id, event_type) VALUES (?,?)",
                (event_id, event_type),
            )
            return True
        except sqlite3.IntegrityError:
            return False
        except Exception as exc:
            if _use_postgres():
                from psycopg.errors import UniqueViolation

                if isinstance(exc, UniqueViolation):
                    return False
            raise


def set_stripe_customer_if_missing(tenant_id: str, customer_id: str) -> str:
    """Atomically set stripe_customer_id; return the winning customer id."""
    with connection() as con:
        con.execute(
            """UPDATE tenants SET stripe_customer_id=?, updated_at=datetime('now')
               WHERE id=? AND (stripe_customer_id IS NULL OR stripe_customer_id='')""",
            (customer_id, tenant_id),
        )
    tenant = get_tenant(tenant_id)
    if tenant and tenant.get("stripe_customer_id"):
        return str(tenant["stripe_customer_id"])
    return customer_id


# --- Storefront tokens ---


def create_storefront_token(
    *,
    token: str,
    tenant_id: str,
    run_id: int,
    label: str | None = None,
    expires_at: str | None = None,
) -> dict:
    with connection() as con:
        con.execute(
            """INSERT INTO storefront_tokens (token, tenant_id, run_id, label, expires_at)
               VALUES (?,?,?,?,?)""",
            (token, tenant_id, run_id, label, expires_at),
        )
    return get_storefront_token(token) or {}


def get_storefront_token(token: str) -> dict | None:
    with connection() as con:
        row = con.execute(
            "SELECT * FROM storefront_tokens WHERE token=?", (token,)
        ).fetchone()
    return dict(row) if row else None


def list_storefront_tokens(tenant_id: str, *, run_id: int | None = None) -> list[dict]:
    with connection() as con:
        if run_id is not None:
            rows = con.execute(
                """SELECT * FROM storefront_tokens
                   WHERE tenant_id=? AND run_id=? ORDER BY created_at DESC""",
                (tenant_id, run_id),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM storefront_tokens WHERE tenant_id=? ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
    return [dict(r) for r in rows]


# --- Orders ---


def create_order(
    *,
    tenant_id: str,
    run_id: int,
    bundle_index: int,
    total_cents: int,
    items: list[dict[str, Any]],
    client_email: str | None = None,
    client_name: str | None = None,
    stripe_session_id: str | None = None,
    client_token: str | None = None,
) -> int:
    import secrets

    token = client_token or secrets.token_urlsafe(18)
    with connection() as con:
        order_id = _insert_id(
            con,
            """INSERT INTO orders
               (tenant_id, run_id, bundle_index, total_cents,
                client_email, client_name, stripe_session_id, client_token)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                tenant_id,
                run_id,
                bundle_index,
                total_cents,
                client_email,
                client_name,
                stripe_session_id,
                token,
            ),
        )
        for item in items:
            con.execute(
                """INSERT INTO order_items (order_id, sku, label, quantity, unit_cents)
                   VALUES (?,?,?,?,?)""",
                (
                    order_id,
                    item["sku"],
                    item["label"],
                    item.get("quantity", 1),
                    item["unit_cents"],
                ),
            )
    return order_id


def get_order(order_id: int, *, tenant_id: str | None = None) -> dict | None:
    with connection() as con:
        if tenant_id:
            row = con.execute(
                "SELECT * FROM orders WHERE id=? AND tenant_id=?",
                (order_id, tenant_id),
            ).fetchone()
        else:
            row = con.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        return None
    out = dict(row)
    with connection() as con2:
        items = con2.execute(
            "SELECT * FROM order_items WHERE order_id=?", (order_id,)
        ).fetchall()
    out["items"] = [dict(i) for i in items]
    return out


def get_order_by_client_token(client_token: str) -> dict | None:
    with connection() as con:
        row = con.execute(
            "SELECT id FROM orders WHERE client_token=?", (client_token,)
        ).fetchone()
    if not row:
        return None
    return get_order(int(row["id"]))


def get_order_by_session(stripe_session_id: str) -> dict | None:
    with connection() as con:
        row = con.execute(
            "SELECT * FROM orders WHERE stripe_session_id=?", (stripe_session_id,)
        ).fetchone()
    if not row:
        return None
    return get_order(int(row["id"]))


def update_order(order_id: int, **fields) -> dict | None:
    allowed = {
        "status",
        "stripe_session_id",
        "stripe_payment_intent",
        "paid_at",
        "client_email",
        "client_name",
        "lab_status",
        "lab_ref",
        "client_token",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return get_order(order_id)
    assignments = ", ".join(f"{key}=?" for key in updates)
    values = list(updates.values()) + [order_id]
    with connection() as con:
        con.execute(f"UPDATE orders SET {assignments} WHERE id=?", values)
    return get_order(order_id)


def mark_order_paid_if_pending(
    order_id: int,
    *,
    stripe_payment_intent: str | None,
    paid_at: str,
    client_email: str | None,
    client_name: str | None,
    client_token: str,
) -> bool:
    """Atomically transition pending→paid. Returns True if this call won the transition."""
    with connection() as con:
        cur = con.execute(
            """UPDATE orders
               SET status='paid',
                   stripe_payment_intent=?,
                   paid_at=?,
                   client_email=COALESCE(?, client_email),
                   client_name=COALESCE(?, client_name),
                   client_token=COALESCE(?, client_token)
               WHERE id=? AND status='pending'""",
            (
                stripe_payment_intent,
                paid_at,
                client_email,
                client_name,
                client_token,
                order_id,
            ),
        )
        return cur.rowcount > 0


def list_orders(*, tenant_id: str | None = None, limit: int = 50) -> list[dict]:
    with connection() as con:
        if tenant_id:
            rows = con.execute(
                "SELECT * FROM orders WHERE tenant_id=? ORDER BY id DESC LIMIT ?",
                (tenant_id, limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


# --- Product overrides ---


def list_product_overrides(tenant_id: str) -> list[dict]:
    with connection() as con:
        rows = con.execute(
            "SELECT * FROM product_overrides WHERE tenant_id=? ORDER BY sku",
            (tenant_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_product_override(
    tenant_id: str,
    sku: str,
    *,
    unit_cents: int | None = None,
    label: str | None = None,
    active: bool = True,
) -> None:
    with connection() as con:
        con.execute(
            """INSERT INTO product_overrides (tenant_id, sku, unit_cents, label, active)
               VALUES (?,?,?,?,?)
               ON CONFLICT(tenant_id, sku) DO UPDATE SET
                 unit_cents=COALESCE(excluded.unit_cents, product_overrides.unit_cents),
                 label=COALESCE(excluded.label, product_overrides.label),
                 active=excluded.active""",
            (tenant_id, sku, unit_cents, label, 1 if active else 0),
        )


# --- Upload batches ---


def create_upload_batch(*, batch_id: str, tenant_id: str, name: str) -> dict:
    with connection() as con:
        con.execute(
            """INSERT INTO upload_batches (id, tenant_id, name, status)
               VALUES (?,?,?, 'open')""",
            (batch_id, tenant_id, name),
        )
    batch = get_upload_batch(batch_id, tenant_id=tenant_id)
    assert batch is not None
    return batch


def get_upload_batch(batch_id: str, *, tenant_id: str | None = None) -> dict | None:
    with connection() as con:
        if tenant_id:
            row = con.execute(
                "SELECT * FROM upload_batches WHERE id=? AND tenant_id=?",
                (batch_id, tenant_id),
            ).fetchone()
        else:
            row = con.execute(
                "SELECT * FROM upload_batches WHERE id=?", (batch_id,)
            ).fetchone()
    return dict(row) if row else None


def update_upload_batch(batch_id: str, **fields) -> dict | None:
    allowed = {
        "name",
        "photo_count",
        "status",
        "run_id",
        "analyze_error",
        "argus_run_id",
        "analyze_started_at",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return get_upload_batch(batch_id)
    assignments = ", ".join(f"{key}=?" for key in updates)
    values = list(updates.values()) + [batch_id]
    with connection() as con:
        con.execute(f"UPDATE upload_batches SET {assignments} WHERE id=?", values)
    return get_upload_batch(batch_id)


def list_upload_batches(*, tenant_id: str, limit: int = 20) -> list[dict]:
    with connection() as con:
        rows = con.execute(
            """SELECT * FROM upload_batches WHERE tenant_id=?
               ORDER BY created_at DESC LIMIT ?""",
            (tenant_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def list_upload_batches_by_status(status: str, *, limit: int = 10) -> list[dict]:
    with connection() as con:
        rows = con.execute(
            """SELECT * FROM upload_batches WHERE status=?
               ORDER BY created_at ASC LIMIT ?""",
            (status, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def claim_upload_batch_for_processing() -> dict | None:
    """Atomically move one queued batch to analyzing."""
    now = datetime.now(UTC).isoformat()
    with connection() as con:
        if _use_postgres():
            row = con.execute(
                """UPDATE upload_batches
                   SET status='analyzing', analyze_error=NULL, analyze_started_at=?
                   WHERE id = (
                       SELECT id FROM upload_batches WHERE status='queued'
                       ORDER BY created_at ASC LIMIT 1
                       FOR UPDATE SKIP LOCKED
                   )
                   RETURNING *""",
                (now,),
            ).fetchone()
        else:
            row = con.execute(
                """SELECT id FROM upload_batches WHERE status='queued'
                   ORDER BY created_at ASC LIMIT 1"""
            ).fetchone()
            if not row:
                return None
            batch_id = row["id"]
            cur = con.execute(
                """UPDATE upload_batches
                   SET status='analyzing', analyze_error=NULL, analyze_started_at=?
                   WHERE id=? AND status='queued'""",
                (now, batch_id),
            )
            if cur.rowcount == 0:
                return None
            row = con.execute(
                "SELECT * FROM upload_batches WHERE id=?", (batch_id,)
            ).fetchone()
    return dict(row) if row else None


def requeue_stale_analyzing_batches(*, stale_before_iso: str) -> int:
    with connection() as con:
        cur = con.execute(
            """UPDATE upload_batches
               SET status='queued',
                   analyze_error='requeued after stale analyze',
                   analyze_started_at=NULL
               WHERE status='analyzing'
                 AND analyze_started_at IS NOT NULL
                 AND analyze_started_at < ?""",
            (stale_before_iso,),
        )
        return cur.rowcount


def insert_ui_session(
    *,
    session_id: str,
    tenant_id: str | None,
    api_key_id: str | None,
    is_admin: bool,
    csrf_token: str,
    expires_at: str,
) -> None:
    with connection() as con:
        con.execute(
            """INSERT INTO ui_sessions
               (id, tenant_id, api_key_id, is_admin, csrf_token, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                tenant_id,
                api_key_id,
                1 if is_admin else 0,
                csrf_token,
                expires_at,
            ),
        )


def get_ui_session(session_id: str) -> dict | None:
    with connection() as con:
        row = con.execute(
            "SELECT * FROM ui_sessions WHERE id=?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def delete_ui_session(session_id: str) -> None:
    with connection() as con:
        con.execute("DELETE FROM ui_sessions WHERE id=?", (session_id,))


def delete_product_override(tenant_id: str, sku: str) -> None:
    with connection() as con:
        con.execute(
            "DELETE FROM product_overrides WHERE tenant_id=? AND sku=?",
            (tenant_id, sku),
        )


# --- Fulfillment timeline ---


def insert_fulfillment_event(
    order_id: int,
    *,
    status: str,
    detail: str | None = None,
) -> None:
    with connection() as con:
        con.execute(
            "INSERT INTO fulfillment_events (order_id, status, detail) VALUES (?,?,?)",
            (order_id, status, detail),
        )


def list_fulfillment_events(order_id: int) -> list[dict]:
    with connection() as con:
        rows = con.execute(
            "SELECT * FROM fulfillment_events WHERE order_id=? ORDER BY id",
            (order_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_order_by_lab_ref(lab_ref: str) -> dict | None:
    with connection() as con:
        row = con.execute("SELECT id FROM orders WHERE lab_ref=?", (lab_ref,)).fetchone()
    if not row:
        return None
    return get_order(int(row["id"]))


def list_orders_pending_lab_poll(*, limit: int = 50) -> list[dict]:
    with connection() as con:
        rows = con.execute(
            """SELECT * FROM orders
               WHERE status='paid' AND lab_status IN ('submitted', 'processing', 'shipped')
               ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]