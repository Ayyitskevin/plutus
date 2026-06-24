"""Shared Redis connection for rate limits, upload-worker locks, and health."""
from __future__ import annotations

import logging
import uuid

from . import config

log = logging.getLogger("plutus.redis")

_client = None
_unavailable = False


def saas_rate_limits_strict() -> bool:
    """SaaS production rate limits must use Redis (fail closed if unavailable)."""
    return bool(config.SAAS_MODE and config.RATE_LIMIT_ENABLED)


def saas_redis_required() -> bool:
    return saas_rate_limits_strict()


def get_client():
    """Return a connected Redis client or None when Redis is not configured."""
    global _client, _unavailable
    if _unavailable or not config.REDIS_URL:
        return None
    if _client is not None:
        return _client
    try:
        import redis
    except ImportError:
        log.warning("PLUTUS_REDIS_URL set but redis package not installed")
        _unavailable = True
        return None
    try:
        _client = redis.from_url(config.REDIS_URL, decode_responses=True)
        _client.ping()
        return _client
    except Exception as exc:
        log.warning("Redis unavailable (%s)", exc)
        _unavailable = True
        return None


def connect_required() -> None:
    """Startup validation — raises when SaaS rate limiting needs Redis."""
    if not saas_redis_required():
        return
    try:
        import redis
    except ImportError as exc:
        raise RuntimeError(
            "PLUTUS_REDIS_URL is set but the redis package is not installed"
        ) from exc
    global _client, _unavailable
    try:
        client = redis.from_url(config.REDIS_URL, decode_responses=True)
        client.ping()
        _client = client
        _unavailable = False
    except Exception as exc:
        raise RuntimeError(f"PLUTUS_REDIS_URL is set but Redis is unreachable: {exc}") from exc


def ping_status() -> dict[str, str | bool]:
    strict = saas_rate_limits_strict()
    if not config.REDIS_URL:
        return {
            "status": "error" if strict else "disabled",
            "configured": False,
            "required": strict,
        }
    client = get_client()
    if client is None:
        required = strict
        return {
            "status": "error" if required else "degraded",
            "configured": True,
            "reachable": False,
            "required": required,
        }
    try:
        client.ping()
        return {
            "status": "ok",
            "configured": True,
            "reachable": True,
            "required": saas_redis_required(),
        }
    except Exception as exc:
        return {
            "status": "error" if saas_redis_required() else "degraded",
            "configured": True,
            "reachable": False,
            "required": saas_redis_required(),
            "detail": str(exc)[:200],
        }


def acquire_lock(name: str, *, ttl_seconds: int = 30) -> bool:
    """Best-effort distributed lock (SET NX EX). Returns True when lock held."""
    client = get_client()
    if client is None:
        return True
    token = uuid.uuid4().hex
    key = f"plutus:lock:{name}"
    try:
        return bool(client.set(key, token, nx=True, ex=ttl_seconds))
    except Exception as exc:
        log.warning("redis lock %s failed (%s) — proceeding without lock", name, exc)
        return True


def release_lock(name: str) -> None:
    client = get_client()
    if client is None:
        return
    try:
        client.delete(f"plutus:lock:{name}")
    except Exception as exc:
        log.warning("redis unlock %s failed (%s)", name, exc)