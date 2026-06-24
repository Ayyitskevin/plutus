"""Tenant registry — API keys, hashing, resolution."""
from __future__ import annotations

import hashlib
import re
import secrets
import uuid

from . import config, db

KEY_PREFIX = "plutus_tk_"
_TENANT_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


class TenantError(Exception):
    """Tenant validation or lookup failure."""


def _hash_key(raw_key: str) -> str:
    payload = f"{config.TENANT_KEY_PEPPER}:{raw_key}".encode()
    return hashlib.sha256(payload).hexdigest()


def generate_api_key(tenant_id: str) -> str:
    token = secrets.token_hex(24)
    return f"{KEY_PREFIX}{tenant_id}_{token}"


def key_prefix_for_tenant(tenant_id: str) -> str:
    return f"{KEY_PREFIX}{tenant_id}"


def tenant_id_from_key(raw_key: str) -> str | None:
    if not raw_key.startswith(KEY_PREFIX):
        return None
    rest = raw_key[len(KEY_PREFIX) :]
    if "_" not in rest:
        return None
    tenant_id, _token = rest.rsplit("_", 1)
    return tenant_id or None


def _normalize_identifier(value: str) -> str:
    return re.sub(r"\s+", "-", value.strip().lower())


def normalize_tenant_id(tenant_id: str) -> str:
    tid = _normalize_identifier(tenant_id)
    if not tid:
        raise TenantError("tenant id required")
    if "_" in tid:
        raise TenantError("tenant id must not contain underscores (reserved for API key format)")
    if not _TENANT_ID_RE.fullmatch(tid):
        raise TenantError(
            "tenant id must be 1-64 chars: lowercase letters, numbers, and hyphens"
        )
    return tid


def normalize_store_slug(slug: str) -> str:
    normalized = _normalize_identifier(slug)
    if not normalized or not _TENANT_ID_RE.fullmatch(normalized):
        raise TenantError(
            "store slug must be 1-64 chars: lowercase letters, numbers, and hyphens"
        )
    return normalized


def create_tenant(
    tenant_id: str,
    *,
    name: str,
    store_slug: str | None = None,
    monthly_recommend_cap: int | None = None,
) -> dict:
    tid = normalize_tenant_id(tenant_id)
    if db.get_tenant(tid):
        raise TenantError(f"tenant already exists: {tid}")
    slug = normalize_store_slug(store_slug or tid)
    if db.get_tenant_by_slug(slug):
        raise TenantError(f"store slug already taken: {slug}")
    return db.create_tenant(
        tid,
        name=name.strip() or tid,
        store_slug=slug,
        monthly_recommend_cap=monthly_recommend_cap,
    )


def issue_api_key(tenant_id: str, *, label: str | None = None) -> dict:
    tenant = db.get_tenant(tenant_id)
    if not tenant:
        raise TenantError(f"tenant not found: {tenant_id}")
    if not tenant["active"]:
        raise TenantError(f"tenant inactive: {tenant_id}")

    raw_key = generate_api_key(tenant_id)
    prefix = key_prefix_for_tenant(tenant_id)
    key_id = str(uuid.uuid4())
    db.insert_tenant_api_key(
        key_id=key_id,
        tenant_id=tenant_id,
        key_prefix=prefix,
        key_hash=_hash_key(raw_key),
        label=label,
    )
    return {
        "key_id": key_id,
        "tenant_id": tenant_id,
        "api_key": raw_key,
        "key_prefix": prefix,
        "label": label,
        "warning": "Store api_key now — it is not persisted in plaintext.",
    }


def resolve_api_key(raw_key: str | None) -> tuple[dict, str] | None:
    if not raw_key or not raw_key.strip():
        return None
    raw_key = raw_key.strip()
    if not raw_key.startswith(KEY_PREFIX):
        return None

    tenant_id = tenant_id_from_key(raw_key)
    if not tenant_id:
        return None
    prefix = key_prefix_for_tenant(tenant_id)
    digest = _hash_key(raw_key)
    for candidate in db.find_tenant_by_key_prefix(prefix):
        if secrets.compare_digest(candidate["key_hash"], digest):
            tenant = db.get_tenant(tenant_id) or candidate["tenant"]
            from . import signup_verify

            if not signup_verify.tenant_email_verified(tenant):
                return None
            return tenant, candidate["key_id"]
    return None


def revoke_key(key_id: str) -> bool:
    return db.revoke_tenant_api_key(key_id)