"""CSRF enforcement for cookie-session UI POSTs."""
from __future__ import annotations

from fastapi import Form, Request

from ..auth import verify_ui_csrf


def require_csrf(request: Request, csrf_token: str = Form("")) -> None:
    verify_ui_csrf(request, csrf_token)