"""Rate-limit client-IP resolution — forwarded headers must not be spoofable.

The per-IP limiter is the only thing standing between an anonymous client and
the signup/login/resend-verification endpoints. If a client can set its own
X-Forwarded-For, it gets a fresh bucket per request and the limiter is useless.
So forwarded headers are honored ONLY when a trusted proxy is configured."""
from __future__ import annotations

from starlette.requests import Request

from app import config, rate_limit


def _request(headers: dict[str, str], client_host: str = "203.0.113.9") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": (client_host, 54321)})


def test_default_ignores_spoofable_forwarded_headers(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_TRUSTED_PROXY", "")
    req = _request(
        {"x-forwarded-for": "1.1.1.1", "cf-connecting-ip": "2.2.2.2"},
        client_host="203.0.113.9",
    )
    # The attacker-controlled headers are ignored; only the socket peer counts,
    # so rotating them cannot mint new buckets.
    assert rate_limit._client_ip(req) == "203.0.113.9"


def test_xff_mode_trusts_first_forwarded_hop(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_TRUSTED_PROXY", "xff")
    req = _request({"x-forwarded-for": "198.51.100.7, 10.0.0.1"})
    assert rate_limit._client_ip(req) == "198.51.100.7"


def test_cloudflare_mode_uses_cf_header_not_spoofed_xff(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_TRUSTED_PROXY", "cloudflare")
    req = _request(
        {"cf-connecting-ip": "198.51.100.7", "x-forwarded-for": "1.1.1.1"},
    )
    # CF-Connecting-IP is set by Cloudflare and cannot be forged through it;
    # a spoofed X-Forwarded-For must not win.
    assert rate_limit._client_ip(req) == "198.51.100.7"


def test_cloudflare_mode_without_cf_header_falls_back_to_peer(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_TRUSTED_PROXY", "cloudflare")
    req = _request({"x-forwarded-for": "1.1.1.1"}, client_host="203.0.113.9")
    # No CF header means the request did not come through Cloudflare; do NOT
    # fall through to the spoofable X-Forwarded-For.
    assert rate_limit._client_ip(req) == "203.0.113.9"
