"""Shared pytest fixtures — isolate tests from production .env Postgres."""
from __future__ import annotations

import pytest

from app import config


@pytest.fixture(autouse=True)
def _sqlite_test_backend(monkeypatch, request):
    """Use per-test SQLite unless test_db_postgres opts into PLUTUS_TEST_DATABASE_URL."""
    if request.node.fspath.basename == "test_db_postgres.py":
        return
    monkeypatch.setattr(config, "DATABASE_URL", None)
    monkeypatch.setattr(config, "DB_BACKEND", "sqlite")
