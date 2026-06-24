"""Tier 10 hardening — webhooks, sessions, upload worker, deferred signup keys."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import billing, config, db, signup, signup_verify, upload_worker


@pytest.fixture()
def saas_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SIGNUP_ENABLED", True)
    monkeypatch.setattr(config, "SIGNUP_VERIFY_EMAIL", True)
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(config, "SMTP_FROM", "verify@plutus.test")
    monkeypatch.setattr(config, "API_TOKEN", "admin-secret")
    monkeypatch.setattr(config, "TENANT_KEY_PEPPER", "pepper-secret")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    db.migrate()
    from app.main import app

    return TestClient(app)


def test_stripe_webhook_retries_after_handler_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    db.migrate()
    from app import tenants

    tenants.create_tenant("billco", name="Bill Co", store_slug="bill-co")
    db.update_tenant("billco", stripe_customer_id="cus_retry", billing_status="active")

    calls = {"n": 0}

    def flaky_update(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated db error")
        return db.get_tenant("billco")

    event = {
        "id": "evt_retry_1",
        "type": "invoice.payment_failed",
        "data": {"object": {"customer": "cus_retry"}},
    }
    with patch("app.billing.db.update_tenant", side_effect=flaky_update):
        with pytest.raises(RuntimeError):
            billing.handle_webhook_event(event)
    assert not db.is_stripe_webhook_processed("evt_retry_1")

    billing.handle_webhook_event(event)
    assert db.is_stripe_webhook_processed("evt_retry_1")
    tenant = db.get_tenant("billco")
    assert tenant["billing_status"] == "past_due"


def test_signup_defers_api_key_until_verify(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SIGNUP_ENABLED", True)
    monkeypatch.setattr(config, "SIGNUP_VERIFY_EMAIL", True)
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(config, "SMTP_FROM", "verify@plutus.test")
    db.migrate()

    with patch("app.notifications._send_email", return_value=True):
        result = signup.register_studio(
            studio_name="Defer Studio",
            email="defer@test.com",
            store_slug="defer-studio",
        )
    assert result["verification_required"] is True
    assert result["api_key"] is None
    pending = db.get_pending_signup_verification_by_email("defer@test.com")
    assert pending and not pending.get("key_id")

    verified = signup_verify.verify_token(pending["token"])
    assert verified["api_key"].startswith("plutus_tk_defer-studio_")


def test_legacy_ui_token_cookie_not_accepted(saas_client):
    from app import tenants

    tenants.create_tenant("legacy", name="Legacy", store_slug="legacy")
    issued = tenants.issue_api_key("legacy")
    db.update_tenant("legacy", email_verified_at=datetime.now(UTC).isoformat())
    saas_client.cookies.set("plutus_ui_token", issued["api_key"])
    r = saas_client.get("/ui/saas/app", follow_redirects=False)
    assert r.status_code == 303
    assert "/ui/saas/login" in r.headers["location"]


def test_login_uses_session_cookie_not_raw_key(saas_client):
    from app import tenants

    tenants.create_tenant("sess", name="Sess", store_slug="sess")
    issued = tenants.issue_api_key("sess")
    db.update_tenant("sess", email_verified_at=datetime.now(UTC).isoformat())

    r = saas_client.post(
        "/ui/saas/login",
        data={"api_token": issued["api_key"]},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "plutus_sid=psess_" in r.headers.get("set-cookie", "")
    assert "plutus_ui_token=" not in r.headers.get("set-cookie", "")

    r = saas_client.get("/ui/saas/app", follow_redirects=False)
    assert r.status_code == 200


def test_csrf_rejects_session_post_without_token(saas_client):
    from app import tenants

    tenants.create_tenant("csrf", name="CSRF Co", store_slug="csrf-co")
    issued = tenants.issue_api_key("csrf")
    db.update_tenant("csrf", email_verified_at=datetime.now(UTC).isoformat())
    saas_client.post(
        "/ui/saas/login",
        data={"api_token": issued["api_key"]},
        follow_redirects=False,
    )

    r = saas_client.post(
        "/ui/saas/app/settings",
        data={"notify_email": "ops@csrf.test", "csrf_token": "not-valid"},
        follow_redirects=False,
    )
    assert r.status_code == 403


def test_csrf_accepts_hidden_field_on_settings(saas_client):
    from app import tenants

    tenants.create_tenant("csrfok", name="CSRF OK", store_slug="csrf-ok")
    issued = tenants.issue_api_key("csrfok")
    db.update_tenant("csrfok", email_verified_at=datetime.now(UTC).isoformat())
    saas_client.post(
        "/ui/saas/login",
        data={"api_token": issued["api_key"]},
        follow_redirects=False,
    )
    r = saas_client.post(
        "/ui/saas/app/settings",
        data={"notify_email": "hello@csrf-ok.test"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "settings_saved=1" in r.headers["location"]


def test_bearer_post_skips_csrf(saas_client):
    from app import tenants

    tenants.create_tenant("api", name="API Co", store_slug="api-co")
    issued = tenants.issue_api_key("api")
    db.update_tenant("api", email_verified_at=datetime.now(UTC).isoformat())
    r = saas_client.post(
        "/recommend/upload-batch",
        data={"batch_id": "missing"},
        headers={"Authorization": f"Bearer {issued['api_key']}"},
        follow_redirects=False,
    )
    assert r.status_code != 403


def test_upload_worker_requeues_stale_analyzing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "UPLOAD_ANALYZE_STALE_MINUTES", 15)
    from app import tenants

    db.migrate()
    tenants.create_tenant("up", name="Up", store_slug="up")
    db.create_upload_batch(batch_id="batch-stale", tenant_id="up", name="Stale")
    stale = (datetime.now(UTC) - timedelta(minutes=20)).isoformat()
    db.update_upload_batch(
        "batch-stale",
        status="analyzing",
        analyze_started_at=stale,
        photo_count=1,
    )
    count = upload_worker.requeue_stale_batches()
    assert count == 1
    batch = db.get_upload_batch("batch-stale")
    assert batch["status"] == "queued"