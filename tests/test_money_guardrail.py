"""Money guardrail — Plutus proposes; it never charges, sends to a client, or invoices.

This is the source-level companion to test_no_money_surface.py (which locks the *routes*).
Here we assert the guarantee at the code/dependency level so a future change can't quietly
reintroduce a charge / client-delivery / invoice path:

  * no payment, billing, or client-messaging SDK is a dependency or imported;
  * no function/class is defined that charges, checks out, settles, refunds, or invoices;
  * the emitted offer is an estimate only — no settlement/charge/paid field on the wire.

The scans are AST-based, so the legitimate guardrail docstrings/comments (e.g. "never sets
a final price or settlement") do not trip them.
"""
from __future__ import annotations

import ast
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from app import config, db, offer_schema, service

_APP_DIR = Path(__file__).resolve().parent.parent / "app"
_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

# SDKs that would mean Plutus is charging a card or messaging the end client directly.
FORBIDDEN_SDKS = {
    "stripe", "braintree", "paypal", "paypalrestsdk", "square", "squareup",
    "authorizenet", "adyen", "twilio", "sendgrid", "postmark", "mailgun",
}

# Identifier stems that would imply a settlement / checkout / client-delivery code path.
FORBIDDEN_DEF_STEMS = (
    "charge", "checkout", "invoice", "settle", "refund", "payout",
    "send_to_client", "email_client", "sms_client", "notify_client", "storefront",
)

# Keys that would turn a proposal into a record of money actually moved.
FORBIDDEN_PAYLOAD_STEMS = (
    "charged", "paid", "settle", "invoice", "payment", "transaction", "stripe",
    "checkout", "refund",
)


def _app_modules() -> list[Path]:
    return sorted(_APP_DIR.rglob("*.py"))


def _parsed() -> list[tuple[Path, ast.Module]]:
    return [(p, ast.parse(p.read_text(), filename=str(p))) for p in _app_modules()]


def _imported_roots(tree: ast.Module) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_no_payment_or_client_messaging_dependency():
    data = tomllib.loads(_PYPROJECT.read_text())
    project = data.get("project", {})
    deps: list[str] = list(project.get("dependencies", []))
    for extra in (project.get("optional-dependencies", {}) or {}).values():
        deps.extend(extra)
    offenders = [
        d for d in deps
        if any(sdk in d.lower() for sdk in FORBIDDEN_SDKS)
    ]
    assert not offenders, f"payment/client-messaging dependency present: {offenders}"


def test_no_payment_or_client_messaging_import():
    offenders: list[str] = []
    for path, tree in _parsed():
        bad = _imported_roots(tree) & FORBIDDEN_SDKS
        if bad:
            offenders.append(f"{path.name}: {sorted(bad)}")
    assert not offenders, f"forbidden SDK imported: {offenders}"


def test_no_charge_invoice_or_client_send_definition():
    offenders: list[str] = []
    for path, tree in _parsed():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = node.name.lower()
                if any(stem in name for stem in FORBIDDEN_DEF_STEMS):
                    offenders.append(f"{path.name}:{node.lineno} {node.name}")
    assert not offenders, f"charge/checkout/invoice/client-send definition found: {offenders}"


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "PUBLIC_URL", "http://plutus.test")
    monkeypatch.setattr(config, "MISE_MEDIA_ROOT", tmp_path / "mise-media")
    db.migrate()
    return tmp_path


def _forbidden_keys(obj: object, *, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            here = f"{path}.{key}" if path else str(key)
            if any(stem in str(key).lower() for stem in FORBIDDEN_PAYLOAD_STEMS):
                found.append(here)
            found.extend(_forbidden_keys(value, path=here))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            found.extend(_forbidden_keys(item, path=f"{path}[{i}]"))
    return found


def test_offer_payload_is_estimate_only(tmp_db, tmp_path):
    gid = 77
    folder = tmp_path / "mise-media" / str(gid) / "original"
    folder.mkdir(parents=True)
    for i in range(14):
        Image.new("RGB", (120, 90)).save(folder / f"img_{i:03d}.jpg")
    row = {
        "id": gid, "title": "Tasting Menu", "published": True,
        "originals_path": str(folder), "argus_last_run_id": None,
    }
    with patch("app.mise_client.get_gallery", return_value=row):
        with patch("app.mise_client.is_enabled", return_value=True):
            result = service.analyze_mise_gallery(gid)

    wire = offer_schema.to_mise_offer(result)
    # Money on the wire is only ever an estimate (estimated_*_cents / unit_cents).
    assert _forbidden_keys(result) == [], _forbidden_keys(result)
    assert _forbidden_keys(wire) == [], _forbidden_keys(wire)
    # Sanity: the estimate fields the contract *does* carry are present.
    assert "estimated_total_cents" in wire
    assert all("estimated_cents" in b for b in wire["bundles"])
