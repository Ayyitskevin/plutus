"""Opaque UI sessions — cookie holds session id, not API keys."""
from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta

from . import config, db

log = logging.getLogger("plutus.ui_sessions")

UI_SESSION_COOKIE = "plutus_sid"
SESSION_PREFIX = "psess_"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _expires_iso() -> str:
    return (datetime.now(UTC) + timedelta(seconds=SESSION_TTL_SECONDS)).isoformat()


def cookie_secure() -> bool:
    return config.SAAS_PUBLIC_URL.lower().startswith("https")


def create_session(
    *,
    is_admin: bool = False,
    tenant_id: str | None = None,
    api_key_id: str | None = None,
) -> tuple[str, str]:
    """Return (session_id, csrf_token)."""
    session_id = SESSION_PREFIX + secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    db.insert_ui_session(
        session_id=session_id,
        tenant_id=tenant_id,
        api_key_id=api_key_id,
        is_admin=is_admin,
        csrf_token=csrf,
        expires_at=_expires_iso(),
    )
    return session_id, csrf


def get_session(session_id: str | None) -> dict | None:
    if not session_id or not session_id.startswith(SESSION_PREFIX):
        return None
    row = db.get_ui_session(session_id.strip())
    if not row:
        return None
    expires = row.get("expires_at")
    if expires:
        try:
            exp_dt = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=UTC)
            if datetime.now(UTC) > exp_dt:
                db.delete_ui_session(session_id)
                return None
        except ValueError:
            db.delete_ui_session(session_id)
            return None
    return row


def delete_session(session_id: str | None) -> None:
    if session_id:
        db.delete_ui_session(session_id.strip())


def validate_csrf(session: dict | None, token: str | None) -> bool:
    if not session or not token:
        return False
    expected = session.get("csrf_token") or ""
    return secrets.compare_digest(expected, token.strip())


def attach_session_cookie(
    response,
    *,
    is_admin: bool,
    tenant_id: str | None,
    api_key_id: str | None,
):
    sid, _csrf = create_session(
        is_admin=is_admin,
        tenant_id=tenant_id,
        api_key_id=api_key_id,
    )
    response.set_cookie(
        UI_SESSION_COOKIE,
        sid,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        max_age=SESSION_TTL_SECONDS,
    )


def csrf_required_path(path: str, method: str) -> bool:
    if method != "POST":
        return False
    if path == "/ui/logout":
        return True
    return path.startswith("/ui/saas/app")