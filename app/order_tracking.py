"""Public client order status — magic link, no login."""
from __future__ import annotations

import secrets

from . import config, db


def new_client_token() -> str:
    return secrets.token_urlsafe(18)


def client_track_url(client_token: str) -> str:
    base = config.SAAS_PUBLIC_URL.rstrip("/")
    return f"{base}/store/order/track/{client_token}"


def ensure_client_token(order_id: int) -> str:
    order = db.get_order(order_id)
    if not order:
        raise ValueError(f"order not found: {order_id}")
    existing = order.get("client_token")
    if existing:
        return str(existing)
    token = new_client_token()
    db.update_order(order_id, client_token=token)
    return token


def resolve_public_order(client_token: str) -> dict | None:
    return db.get_order_by_client_token(client_token)