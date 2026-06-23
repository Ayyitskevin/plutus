"""Persistence for galleries, recommendations, tenants, and orders (SQLite or Postgres)."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
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
        tenant_cols = {r[1] for r in con.execute("PRAGMA table_info(tenants)")}
        if "notify_email" not in tenant_cols:
            con.execute("ALTER TABLE tenants ADD COLUMN notify_email TEXT")
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
        for col, typ in [("analyze_error", "TEXT"), ("argus_run_id", "INTEGER")]:
            if col not in batch_cols:
                con.execute(f"ALTER TABLE upload_batches ADD COLUMN {col} {typ}")


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
                      t.billing_status, t.plan_tier
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
                 recommends = recommends + excluded.recommends,
                 orders = orders + excluded.orders,
                 revenue_cents = revenue_cents + excluded.revenue_cents,
                 updated_at = datetime('now')""",
            (tenant_id, period, recommends, orders, revenue_cents),
        )
    return get_tenant_usage(tenant_id, period)


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


# --- Stripe webhook dedup ---


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
    allowed = {"name", "photo_count", "status", "run_id", "analyze_error", "argus_run_id"}
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