"""Postgres backend — skipped unless PLUTUS_TEST_DATABASE_URL is set."""
from __future__ import annotations

import os

import pytest

from app import config, db, tenants

PG_URL = os.environ.get("PLUTUS_TEST_DATABASE_URL")


@pytest.fixture()
def pg_env(tmp_path, monkeypatch):
    if not PG_URL:
        pytest.skip("PLUTUS_TEST_DATABASE_URL not set")
    monkeypatch.setattr(config, "DATABASE_URL", PG_URL)
    monkeypatch.setattr(config, "DB_BACKEND", "postgres")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "unused.db")
    db.migrate()
    yield
    with db.connection() as con:
        con.execute(
            "TRUNCATE fulfillment_events, order_items, orders, storefront_tokens, "
            "upload_batches, product_overrides, tenant_api_keys, tenant_usage, "
            "audit_log, stripe_webhook_events, recommendation_runs, galleries, "
            "tenants RESTART IDENTITY CASCADE"
        )


def test_postgres_ping_and_migrate(pg_env):
    assert db.backend_name() == "postgres"
    assert db.ping()


def test_postgres_storefront_token_json_safe(pg_env):
    tenants.create_tenant("pgco", name="PG Co", store_slug="pg-co")
    gid = db.insert_gallery(name="G", source="/x", photo_count=1, tenant_id="pgco")
    rid = db.insert_run(
        gallery_id=gid,
        engine="mock",
        bundle_count=1,
        estimated_total_cents=1200,
        payload={"bundles": []},
        tenant_id="pgco",
    )
    row = db.create_storefront_token(
        token="tok123",
        tenant_id="pgco",
        run_id=rid,
        label="demo",
    )
    import json

    json.dumps(row)
    assert row["created_at"]


def test_postgres_increment_tenant_usage(pg_env):
    tenants.create_tenant("pgco", name="PG Co", store_slug="pg-co")
    db.increment_tenant_usage("pgco", recommends=2, orders=1, revenue_cents=500)
    usage = db.get_tenant_usage("pgco")
    assert usage["recommends"] == 2
    assert usage["orders"] == 1
    assert usage["revenue_cents"] == 500
    db.increment_tenant_usage("pgco", recommends=1)
    usage = db.get_tenant_usage("pgco")
    assert usage["recommends"] == 3


def test_postgres_tenant_and_run_roundtrip(pg_env):
    tenants.create_tenant("pgco", name="PG Co", store_slug="pg-co")
    gid = db.insert_gallery(name="G", source="/x", photo_count=2, tenant_id="pgco")
    rid = db.insert_run(
        gallery_id=gid,
        engine="mock",
        bundle_count=1,
        estimated_total_cents=1200,
        payload={"bundles": [{"title": "A", "items": []}], "gallery_theme": "wedding"},
        tenant_id="pgco",
    )
    row = db.get_run(rid, tenant_id="pgco")
    assert row is not None
    assert row["payload"]["gallery_theme"] == "wedding"
    runs = db.list_runs(tenant_id="pgco", limit=5)
    assert runs[0]["gallery_theme"] == "wedding"