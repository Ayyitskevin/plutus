"""Shared route dependencies — templates and studio UI context."""
from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .. import config

_ROOT = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(_ROOT / "templates"))


def _fmt_cents(cents: int) -> str:
    return f"${cents / 100:,.2f}"


templates.env.filters["money"] = _fmt_cents


def ui_context(request: Request | None = None, **extra) -> dict:
    """Base template context for studio pages. No tenant / SaaS / CSRF state."""
    from .. import argus_client, mise_client

    del request  # studio mode has no UI session or CSRF token
    ctx = {
        "csrf_token": "",
        "argus": argus_client.vision_status() if argus_client.is_enabled() else None,
        "argus_auto_vision": config.ARGUS_AUTO_VISION,
        "mise_configured": mise_client.is_enabled(),
        "public_base": config.PUBLIC_URL.rstrip("/"),
    }
    ctx.update(extra)
    return ctx
