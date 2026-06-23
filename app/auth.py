"""Optional bearer auth for inbound Mise → Plutus calls."""
from __future__ import annotations

import secrets

from fastapi import HTTPException, Request

from . import config


def require_token(request: Request) -> None:
    if not config.API_TOKEN:
        return
    header = request.headers.get("Authorization", "")
    expected = f"Bearer {config.API_TOKEN}"
    if not secrets.compare_digest(header, expected):
        raise HTTPException(status_code=401, detail="bad token")