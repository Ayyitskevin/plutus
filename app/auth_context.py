"""Request auth context for the single-operator Mise worker (admin token only)."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class AuthContext:
    is_admin: bool = False


_auth_ctx: ContextVar[AuthContext | None] = ContextVar("auth_ctx", default=None)


def set_auth_context(ctx: AuthContext | None) -> None:
    _auth_ctx.set(ctx)


def get_auth_context() -> AuthContext | None:
    return _auth_ctx.get()


def is_admin() -> bool:
    ctx = get_auth_context()
    return bool(ctx and ctx.is_admin)
