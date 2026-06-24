"""Shared pytest fixtures — isolate tests from production .env Postgres."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app import config, ui_sessions

_CSRF_SKIP_PREFIXES = (
    "/ui/saas/login",
    "/ui/saas/signup",
    "/ui/saas/resend-verification",
)


_ORIGINAL_TESTCLIENT_POST = TestClient.post


@pytest.fixture(autouse=True)
def _inject_csrf_on_ui_post(monkeypatch):
    """Attach session CSRF token to UI POSTs when a plutus_sid cookie is present."""
    orig_post = _ORIGINAL_TESTCLIENT_POST

    def post(self, url, *args, **kwargs):
        if (
            isinstance(url, str)
            and url.startswith("/ui/")
            and not any(url.startswith(p) for p in _CSRF_SKIP_PREFIXES)
        ):
            sid = self.cookies.get(ui_sessions.UI_SESSION_COOKIE)
            if sid:
                session = ui_sessions.get_session(sid)
                token = (session or {}).get("csrf_token")
                if token:
                    data = kwargs.get("data")
                    if data is None:
                        kwargs["data"] = {"csrf_token": token}
                    elif isinstance(data, dict) and "csrf_token" not in data:
                        kwargs["data"] = {**data, "csrf_token": token}
        return orig_post(self, url, *args, **kwargs)

    monkeypatch.setattr(TestClient, "post", post)


@pytest.fixture(autouse=True)
def _sqlite_test_backend(monkeypatch, request):
    """Use per-test SQLite unless test_db_postgres opts into PLUTUS_TEST_DATABASE_URL."""
    if request.node.fspath.basename == "test_db_postgres.py":
        return
    monkeypatch.setattr(config, "DATABASE_URL", None)
    monkeypatch.setattr(config, "DB_BACKEND", "sqlite")
    # HTTP testserver cannot send Secure cookies set when SAAS_PUBLIC_URL is https
    monkeypatch.setattr(config, "SAAS_PUBLIC_URL", "http://testserver")