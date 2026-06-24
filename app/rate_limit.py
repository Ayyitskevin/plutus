"""Per-tenant and per-IP rate limiting for SaaS mode."""
from __future__ import annotations

import logging
import sys
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response

from . import config, metrics, redis_client
from .auth import resolve_auth
from .auth_context import AuthContext

log = logging.getLogger("plutus.rate_limit")

_lock = Lock()
_windows: dict[str, deque[float]] = defaultdict(deque)

RECOMMEND_PATHS = frozenset({"/analyze-folder", "/recommend/mise-gallery", "/analyze"})
SIGNUP_PATHS = frozenset({"/ui/saas/signup"})
LOGIN_PATHS = frozenset({"/ui/saas/login"})
RESEND_VERIFY_PATHS = frozenset({"/ui/saas/resend-verification"})
WINDOW_SECONDS = 60


def _client_key(request: Request, ctx: AuthContext | None) -> str:
    if ctx and ctx.tenant_id:
        return f"tenant:{ctx.tenant_id}"
    if ctx and ctx.is_admin:
        return "admin"
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if forwarded:
        return f"ip:{forwarded}"
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}"


def _limit_for(request: Request) -> int:
    if request.url.path in SIGNUP_PATHS and request.method == "POST":
        return min(config.RATE_LIMIT_PER_MINUTE, 10)
    if request.url.path in LOGIN_PATHS and request.method == "POST":
        return min(config.RATE_LIMIT_PER_MINUTE, 5)
    if request.url.path in RESEND_VERIFY_PATHS and request.method == "POST":
        return min(config.RATE_LIMIT_PER_MINUTE, 3)
    if request.url.path in RECOMMEND_PATHS:
        return config.RATE_LIMIT_RECOMMEND_PER_MINUTE
    return config.RATE_LIMIT_PER_MINUTE


def validate_rate_limit_backend() -> None:
    """Fail fast in multi-worker SaaS when shared rate-limit state is unavailable."""
    if not config.SAAS_MODE or not config.RATE_LIMIT_ENABLED:
        return
    if "pytest" in sys.modules:
        return
    if not config.REDIS_URL:
        raise RuntimeError(
            "PLUTUS_REDIS_URL required when PLUTUS_SAAS_MODE and PLUTUS_RATE_LIMIT_ENABLED "
            "(in-memory limits are per-process only)"
        )
    redis_client.connect_required()


def _check_memory(key: str, limit: int) -> tuple[bool, int, int, bool]:
    now = time.time()
    with _lock:
        bucket = _windows[key]
        while bucket and now - bucket[0] > WINDOW_SECONDS:
            bucket.popleft()
        count = len(bucket)
        if count >= limit:
            retry_after = int(WINDOW_SECONDS - (now - bucket[0])) + 1
            return False, max(retry_after, 1), 0, False
        bucket.append(now)
        remaining = max(limit - len(bucket), 0)
        return True, 0, remaining, False


def _check_redis(key: str, limit: int) -> tuple[bool, int, int, bool]:
    client = redis_client.get_client()
    if client is None:
        if redis_client.saas_redis_required():
            return False, 60, 0, True
        return _check_memory(key, limit)

    now = time.time()
    bucket = int(now // 60)
    redis_key = f"plutus:rl:{key}:{bucket}"
    try:
        count = int(client.incr(redis_key))
        if count == 1:
            client.expire(redis_key, 120)
        if count > limit:
            retry_after = int(60 - (now % 60)) + 1
            return False, max(retry_after, 1), 0, False
        return True, 0, max(limit - count, 0), False
    except Exception as exc:
        log.warning("redis rate-limit error (%s)", exc)
        if redis_client.saas_redis_required():
            return False, 60, 0, True
        return _check_memory(key, limit)


def _check(key: str, limit: int) -> tuple[bool, int, int, bool]:
    if config.REDIS_URL:
        return _check_redis(key, limit)
    return _check_memory(key, limit)


def _rate_limit_headers(limit: int, remaining: int, retry_after: int = 0) -> dict[str, str]:
    headers = {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Window": str(WINDOW_SECONDS),
    }
    if retry_after:
        headers["Retry-After"] = str(retry_after)
    return headers


def attach_rate_limit_headers(response: Response, *, limit: int, remaining: int) -> None:
    for key, value in _rate_limit_headers(limit, remaining).items():
        response.headers[key] = value


async def rate_limit_middleware(request: Request, call_next):
    if not config.SAAS_MODE or not config.RATE_LIMIT_ENABLED:
        return await call_next(request)

    ctx: AuthContext | None = getattr(request.state, "auth", None)
    if ctx is None and request.headers.get("Authorization"):
        try:
            ctx = resolve_auth(request, authorization=request.headers.get("Authorization"))
            request.state.auth = ctx
        except HTTPException:
            ctx = None
    key = _client_key(request, ctx)
    limit = _limit_for(request)
    ok, retry_after, remaining, backend_down = _check(key, limit)
    if backend_down:
        return JSONResponse(
            {"error": "rate limit backend unavailable"},
            status_code=503,
        )
    if not ok:
        metrics.inc("rate_limit_exceeded")
        return JSONResponse(
            {"error": "rate limit exceeded", "retry_after_seconds": retry_after},
            status_code=429,
            headers=_rate_limit_headers(limit, 0, retry_after),
        )
    response = await call_next(request)
    attach_rate_limit_headers(response, limit=limit, remaining=remaining)
    return response