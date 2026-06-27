"""Inbound service-token register for the Mise recommend / callback path.

The recommend path historically accepted *either* ``PLUTUS_API_TOKEN`` or
``PLUTUS_MISE_HOOK_TOKEN``, compared inconsistently (one constant-time, one not).
Drift between Mise's ``MISE_PLUTUS_TOKEN`` and these two has caused recurring 401
outages on publish. This module is the single source of truth: any configured
token is accepted, all compared in constant time, and extra rotation tokens can be
supplied via ``PLUTUS_SERVICE_TOKENS`` so swapping a secret never strands the
publish path with a hard 401.

Part of the worker contract: the service-token register is the auth surface Mise
relies on. Keep it dependency-free and constant-time.
"""
from __future__ import annotations

import secrets

from . import config


def registered_tokens() -> list[str]:
    """All accepted inbound tokens, de-duplicated, order-stable."""
    tokens: list[str] = []
    for tok in (config.API_TOKEN, config.MISE_HOOK_TOKEN, *config.SERVICE_TOKENS):
        if tok and tok not in tokens:
            tokens.append(tok)
    return tokens


def auth_required() -> bool:
    """False only when no token is configured (open studio-dev default)."""
    return bool(registered_tokens())


def verify(provided: str | None) -> bool:
    """Constant-time check of ``provided`` against the whole register.

    Returns True when no token is configured (auth disabled). The loop does not
    short-circuit, so response timing does not leak which token matched.
    """
    tokens = registered_tokens()
    if not tokens:
        return True
    if not provided:
        return False
    matched = False
    for tok in tokens:
        if secrets.compare_digest(provided, tok):
            matched = True
    return matched
