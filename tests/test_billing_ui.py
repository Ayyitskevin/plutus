"""SaaS billing UI — cookie auth checkout + subscription summary."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import billing, config, db, tenants


@pytest.fixture()
def saas_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "API_TOKEN", "admin-secret")
    monkeypatch.setattr(config, "TENANT_KEY_PEPPER", "pepper-secret")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setattr(config, "STRIPE_PRICE_ID", "price_test_fake")
    db.migrate()
    from app.main import app

    return TestClient(app)


def test_billing_page_requires_tenant_login(saas_client):
    r = saas_client.get("/ui/saas/billing", follow_redirects=False)
    assert r.status_code == 303
    assert "/ui/saas/login" in r.headers["location"]


def test_billing_checkout_cookie_auth(saas_client):
    tenants.create_tenant("billui", name="Bill UI", store_slug="bill-ui")
    issued = tenants.issue_api_key("billui")
    saas_client.post(
        "/ui/saas/login",
        data={"api_token": issued["api_key"]},
        follow_redirects=False,
    )
    r = saas_client.get("/ui/saas/billing")
    assert r.status_code == 200
    assert b"Upgrade to Pro" in r.content or b"Subscribe" in r.content

    with patch(
        "app.billing.create_checkout_session",
        return_value={"checkout_url": "https://checkout.stripe.test/sub", "session_id": "cs_test"},
    ):
        r = saas_client.post("/ui/saas/billing/checkout", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "https://checkout.stripe.test/sub"


def test_tenant_subscription_view(monkeypatch):
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setattr(config, "STRIPE_PRICE_ID", "price_test_fake")
    tenant = {
        "billing_status": "trialing",
        "plan_tier": "trial",
        "monthly_recommend_cap": 25,
    }
    view = billing.tenant_subscription_view(tenant)
    assert view["can_subscribe"] is True
    assert view["is_active"] is False