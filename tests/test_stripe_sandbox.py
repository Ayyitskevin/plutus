"""Live Stripe charges require PLUTUS_STRIPE_LIVE_ENABLED."""
from __future__ import annotations

import pytest

from app import billing, config, orders


def test_live_keys_blocked_without_opt_in(monkeypatch):
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_live_sandbox_test")
    monkeypatch.setattr(config, "STRIPE_LIVE_ENABLED", False)
    assert billing.stripe_configured()
    assert not billing.stripe_test_mode()
    assert not billing.payments_allowed()
    assert not billing.billing_enabled()


def test_live_keys_allowed_when_opted_in(monkeypatch):
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_live_sandbox_test")
    monkeypatch.setattr(config, "STRIPE_PRICE_ID", "price_test123")
    monkeypatch.setattr(config, "STRIPE_LIVE_ENABLED", True)
    assert billing.payments_allowed()
    assert billing.billing_enabled()


def test_test_keys_always_allowed(monkeypatch):
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_sandbox")
    monkeypatch.setattr(config, "STRIPE_LIVE_ENABLED", False)
    assert billing.payments_allowed()


def test_create_bundle_checkout_rejects_live_without_opt_in(monkeypatch):
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_live_sandbox_test")
    monkeypatch.setattr(config, "STRIPE_LIVE_ENABLED", False)
    with pytest.raises(orders.OrderError, match="disabled"):
        orders.create_bundle_checkout(
            tenant_id="t",
            run_id=1,
            bundle_index=0,
            client_email="c@example.com",
        )