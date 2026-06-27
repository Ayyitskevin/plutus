"""Postgres backend — skipped unless PLUTUS_TEST_DATABASE_URL is set."""
from __future__ import annotations

import os

import pytest

from app import config, db

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
            "TRUNCATE callback_deadletter, recommendation_runs, galleries "
            "RESTART IDENTITY CASCADE"
        )


def _schema_snapshot() -> dict[str, set[str]]:
    """{table_name: {column_names}} for the CURRENT backend, after migrate().

    Compares structure across backends, so it reads only names — never types
    (TEXT vs TIMESTAMPTZ legitimately differ and must not trip the diff)."""
    snap: dict[str, set[str]] = {}
    with db.connection() as con:
        if db.backend_name() == "postgres":
            tables = [
                r["table_name"]
                for r in con.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_type='BASE TABLE'"
                ).fetchall()
            ]
            for t in tables:
                rows = con.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name = ?",
                    (t,),
                ).fetchall()
                snap[t] = {r["column_name"] for r in rows}
        else:
            tables = [
                r["name"]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            ]
            for t in tables:
                rows = con.execute(f"PRAGMA table_info({t})").fetchall()
                snap[t] = {r[1] for r in rows}
    return snap


def test_schema_parity_sqlite_vs_postgres(tmp_path, monkeypatch):
    """The SQLite (homelab) and Postgres (SaaS prod) schemas are maintained as two
    hand-edited DDL blocks plus two separate migrate() paths. They MUST stay
    structurally identical, table-for-table and column-for-column: a column added
    to one backend and forgotten on the other is a silent production break on
    whichever backend the author didn't test. This pins that invariant so the
    drift fails CI here instead of in prod.
    """
    if not PG_URL:
        pytest.skip("PLUTUS_TEST_DATABASE_URL not set")

    # Postgres (the production SaaS backend).
    monkeypatch.setattr(config, "DATABASE_URL", PG_URL)
    monkeypatch.setattr(config, "DB_BACKEND", "postgres")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "unused.db")
    db.migrate()
    pg = _schema_snapshot()

    # SQLite (the homelab + test backend), built fresh in a throwaway file.
    monkeypatch.setattr(config, "DATABASE_URL", None)
    monkeypatch.setattr(config, "DB_BACKEND", "sqlite")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "parity.db")
    db.migrate()
    sqlite = _schema_snapshot()

    assert set(pg) == set(sqlite), (
        "table set differs between backends — "
        f"only in postgres: {sorted(set(pg) - set(sqlite))}; "
        f"only in sqlite: {sorted(set(sqlite) - set(pg))}"
    )

    drift = {
        t: {
            "only_postgres": sorted(pg[t] - sqlite[t]),
            "only_sqlite": sorted(sqlite[t] - pg[t]),
        }
        for t in sorted(set(pg) & set(sqlite))
        if pg[t] != sqlite[t]
    }
    assert not drift, f"column drift between SQLite and Postgres schemas: {drift}"


def test_postgres_ping_and_migrate(pg_env):
    assert db.backend_name() == "postgres"
    assert db.ping()


def test_postgres_mise_gallery_idempotency_helpers(pg_env):
    """Exercise the PR3 idempotency lookups on real Postgres (conftest forces
    SQLite everywhere else, so this is the only Postgres coverage for them)."""
    gid = db.insert_gallery(name="Tasting", source="/x", photo_count=3, mise_gallery_id=99)
    rid = db.insert_run(
        gallery_id=gid,
        engine="mock",
        bundle_count=1,
        estimated_total_cents=1000,
        payload={"bundles": []},
    )
    # Lookup by Mise id (studio scope: tenant_id IS NULL) finds the same gallery/run.
    found = db.get_gallery_by_mise_id(99)
    assert found is not None and int(found["id"]) == gid
    assert db.run_id_for_gallery(gid) == rid
    # A tenant-scoped lookup must NOT see the studio (NULL-tenant) gallery.
    assert db.get_gallery_by_mise_id(99, tenant_id="pgco") is None
    # update_gallery refreshes mutable metadata.
    db.update_gallery(gid, name="Renamed", photo_count=8)
    refreshed = db.get_gallery(gid)
    assert refreshed["name"] == "Renamed"
    assert refreshed["photo_count"] == 8


def test_postgres_run_json_roundtrip(pg_env):
    gid = db.insert_gallery(name="G", source="/x", photo_count=2)
    rid = db.insert_run(
        gallery_id=gid,
        engine="mock",
        bundle_count=1,
        estimated_total_cents=1200,
        payload={"bundles": [{"title": "A", "items": []}], "gallery_theme": "wedding"},
    )
    row = db.get_run(rid)
    assert row is not None
    assert row["payload"]["gallery_theme"] == "wedding"
    runs = db.list_runs(limit=5)
    assert runs[0]["gallery_theme"] == "wedding"