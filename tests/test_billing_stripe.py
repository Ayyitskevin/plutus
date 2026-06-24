"""Stripe webhook hardening — tenant resolution and past_due."""
from __future__ import annotations

import pytest

from app import billing, config, db, tenants


@pytest.fixture()
def saas_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    db.migrate()


def test_invoice_payment_failed_marks_past_due(saas_db):
    tenants.create_tenant("billco", name="Bill Co", store_slug="bill-co")
    db.update_tenant("billco", stripe_customer_id="cus_test_123", billing_status="active")

    billing.handle_webhook_event(
        {
            "id": "evt_fail_1",
            "type": "invoice.payment_failed",
            "data": {"object": {"customer": "cus_test_123"}},
        }
    )
    tenant = db.get_tenant("billco")
    assert tenant is not None
    assert tenant["billing_status"] == "past_due"
    assert tenant["active"] is False


def test_checkout_subscription_activates_tenant(saas_db):
    tenants.create_tenant("subco", name="Sub Co", store_slug="sub-co")
    billing.handle_webhook_event(
        {
            "id": "evt_sub_checkout",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_sub",
                    "customer": "cus_new",
                    "subscription": "sub_new",
                    "metadata": {
                        "tenant_id": "subco",
                        "checkout_kind": "tenant_subscription",
                    },
                }
            },
        }
    )
    tenant = db.get_tenant("subco")
    assert tenant["billing_status"] == "active"
    assert tenant["plan_tier"] == "pro"
    assert tenant["monthly_recommend_cap"] == 500
    assert db.is_stripe_webhook_processed("evt_sub_checkout")


def test_subscription_updated_resolves_by_customer_id(saas_db):
    tenants.create_tenant("subco", name="Sub Co", store_slug="sub-co")
    db.update_tenant("subco", stripe_customer_id="cus_sub_99")

    billing.handle_webhook_event(
        {
            "id": "evt_sub_1",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_abc",
                    "customer": "cus_sub_99",
                    "status": "active",
                    "metadata": {},
                }
            },
        }
    )
    tenant = db.get_tenant("subco")
    assert tenant is not None
    assert tenant["stripe_subscription_id"] == "sub_abc"
    assert tenant["billing_status"] == "active"