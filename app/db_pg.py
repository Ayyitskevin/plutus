"""PostgreSQL backend — SQL adapter over the shared SQLite-oriented query layer."""
from __future__ import annotations

import logging
import secrets
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

CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    store_slug TEXT UNIQUE,
    active INTEGER NOT NULL DEFAULT 1,
    monthly_recommend_cap INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    billing_status TEXT,
    plan_tier TEXT DEFAULT 'trial',
    notify_email TEXT,
    email_verified_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS signup_verifications (
    token TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    email TEXT NOT NULL,
    key_id TEXT,
    api_key TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    verified_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_signup_verify_tenant
    ON signup_verifications(tenant_id, created_at);

CREATE TABLE IF NOT EXISTS tenant_api_keys (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    key_prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    label TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_tenant_keys_prefix ON tenant_api_keys(key_prefix);

CREATE TABLE IF NOT EXISTS tenant_usage (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    period TEXT NOT NULL,
    recommends INTEGER NOT NULL DEFAULT 0,
    orders INTEGER NOT NULL DEFAULT 0,
    revenue_cents INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, period)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
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
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS storefront_tokens (
    token TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    run_id INTEGER NOT NULL REFERENCES recommendation_runs(id),
    label TEXT,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_storefront_tenant ON storefront_tokens(tenant_id, run_id);

CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    run_id INTEGER NOT NULL REFERENCES recommendation_runs(id),
    bundle_index INTEGER NOT NULL DEFAULT 0,
    stripe_session_id TEXT,
    stripe_payment_intent TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    total_cents INTEGER NOT NULL DEFAULT 0,
    client_email TEXT,
    client_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    paid_at TIMESTAMPTZ,
    lab_status TEXT,
    lab_ref TEXT,
    client_token TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_tenant ON orders(tenant_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_client_token
    ON orders(client_token) WHERE client_token IS NOT NULL;

CREATE TABLE IF NOT EXISTS order_items (
    id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    sku TEXT NOT NULL,
    label TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    unit_cents INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS product_overrides (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    sku TEXT NOT NULL,
    unit_cents INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    label TEXT,
    PRIMARY KEY (tenant_id, sku)
);

CREATE TABLE IF NOT EXISTS fulfillment_events (
    id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    status TEXT NOT NULL,
    detail TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fulfillment_order ON fulfillment_events(order_id, created_at);

CREATE TABLE IF NOT EXISTS upload_batches (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    name TEXT NOT NULL,
    photo_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open',
    run_id INTEGER,
    analyze_error TEXT,
    argus_run_id INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_upload_batches_tenant ON upload_batches(tenant_id, created_at);
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
        con.execute(
            "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ"
        )
        con.execute(
            "ALTER TABLE signup_verifications ADD COLUMN IF NOT EXISTS key_id TEXT"
        )
        con.execute(
            "ALTER TABLE signup_verifications ALTER COLUMN api_key DROP NOT NULL"
        )
        con.execute(
            "ALTER TABLE upload_batches ADD COLUMN IF NOT EXISTS analyze_started_at TIMESTAMPTZ"
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS ui_sessions (
                id TEXT PRIMARY KEY,
                tenant_id TEXT,
                api_key_id TEXT,
                is_admin INTEGER NOT NULL DEFAULT 0,
                csrf_token TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL
            )"""
        )
        for stmt in (
            "CREATE INDEX IF NOT EXISTS idx_upload_batches_status "
            "ON upload_batches(status, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_tenants_stripe_customer ON tenants(stripe_customer_id)",
            "CREATE INDEX IF NOT EXISTS idx_orders_stripe_session ON orders(stripe_session_id)",
            "CREATE INDEX IF NOT EXISTS idx_orders_lab_poll ON orders(status, lab_status)",
        ):
            con.execute(stmt)
        con.execute(
            "UPDATE tenants SET email_verified_at=created_at "
            "WHERE email_verified_at IS NULL"
        )
        missing = con.execute(
            "SELECT id FROM orders WHERE client_token IS NULL OR client_token = ''"
        ).fetchall()
        for row in missing:
            con.execute(
                "UPDATE orders SET client_token=%s WHERE id=%s",
                (secrets.token_urlsafe(18), row["id"]),
            )
    log.info("postgres schema migrated")