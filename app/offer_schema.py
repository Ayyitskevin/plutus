"""Canonical Mise offer contract — the JSON Plutus hands back for a gallery.

This is the single source of truth for the recommendation worker contract. Mise
links an accepted offer to a real invoice line via the per-bundle ``sku`` and the
catalog ``sku`` on each line item, so it can attribute true upsell revenue instead
of a coarse project-level proxy. Keep this module dependency-free (no jsonschema)
so it can be imported anywhere and asserted in mock-only CI.

Money guardrail: every ``*_cents`` value here is a PROPOSAL a human reviews and
applies in Mise. Plutus never sets a final price or settlement.
"""
from __future__ import annotations

from typing import Any

# Bump when the wire shape changes in a non-backward-compatible way.
OFFER_SCHEMA_VERSION = "1"


def _is_int(value: Any) -> bool:
    # bool is an int subclass — exclude it so True/False can't pose as cents.
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_line_item(item: Any, where: str) -> list[str]:
    problems: list[str] = []
    if not isinstance(item, dict):
        return [f"{where}: line_item must be an object"]
    # Minimal contract: label, qty, unit_cents. `sku` is a Plutus superset that
    # carries the catalog product key Mise maps to an invoice-line product.
    if not isinstance(item.get("label"), str) or not item["label"]:
        problems.append(f"{where}: line_item.label must be a non-empty string")
    if not _is_int(item.get("qty")) or item["qty"] < 1:
        problems.append(f"{where}: line_item.qty must be a positive int")
    if not _is_int(item.get("unit_cents")) or item["unit_cents"] < 0:
        problems.append(f"{where}: line_item.unit_cents must be a non-negative int")
    if "sku" in item and not (isinstance(item["sku"], str) and item["sku"]):
        problems.append(f"{where}: line_item.sku, when present, must be a non-empty string")
    return problems


def _validate_bundle(bundle: Any, where: str) -> list[str]:
    problems: list[str] = []
    if not isinstance(bundle, dict):
        return [f"{where}: bundle must be an object"]
    if not isinstance(bundle.get("sku"), str) or not bundle["sku"]:
        problems.append(f"{where}: bundle.sku must be a stable non-empty string")
    if not isinstance(bundle.get("label"), str) or not bundle["label"]:
        problems.append(f"{where}: bundle.label must be a non-empty string")
    if not _is_int(bundle.get("estimated_cents")) or bundle["estimated_cents"] < 0:
        problems.append(f"{where}: bundle.estimated_cents must be a non-negative int")
    line_items = bundle.get("line_items")
    if not isinstance(line_items, list) or not line_items:
        problems.append(f"{where}: bundle.line_items must be a non-empty list")
        return problems
    computed = 0
    for idx, line in enumerate(line_items):
        problems.extend(_validate_line_item(line, f"{where}.line_items[{idx}]"))
        if isinstance(line, dict) and _is_int(line.get("qty")) and _is_int(line.get("unit_cents")):
            computed += line["qty"] * line["unit_cents"]
    if not problems and computed != bundle["estimated_cents"]:
        problems.append(
            f"{where}: bundle.estimated_cents ({bundle['estimated_cents']}) "
            f"!= sum(qty*unit_cents) ({computed})"
        )
    return problems


def validate_offer(payload: Any) -> list[str]:
    """Return a list of contract problems (empty list == valid)."""
    problems: list[str] = []
    if not isinstance(payload, dict):
        return ["offer must be an object"]

    if not _is_int(payload.get("run_id")):
        problems.append("run_id must be an int")
    if not _is_int(payload.get("estimated_total_cents")) or payload["estimated_total_cents"] < 0:
        problems.append("estimated_total_cents must be a non-negative int")
    for url_key in ("offer_url", "pitch_url"):
        if not isinstance(payload.get(url_key), str) or not payload[url_key]:
            problems.append(f"{url_key} must be a non-empty string")

    # Provenance — persisted to Mise's ai_runs ledger / cost report.
    if not isinstance(payload.get("model"), str) or not payload["model"]:
        problems.append("model must be a non-empty string")
    if not _is_int(payload.get("latency_ms")) or payload["latency_ms"] < 0:
        problems.append("latency_ms must be a non-negative int")
    if not isinstance(payload.get("cost_usd"), (int, float)) or isinstance(
        payload.get("cost_usd"), bool
    ):
        problems.append("cost_usd must be a number")

    bundles = payload.get("bundles")
    if not isinstance(bundles, list):
        problems.append("bundles must be a list")
        return problems
    bundle_total = 0
    for idx, bundle in enumerate(bundles):
        bundle_problems = _validate_bundle(bundle, f"bundles[{idx}]")
        problems.extend(bundle_problems)
        if not bundle_problems and isinstance(bundle, dict):
            bundle_total += bundle["estimated_cents"]
    if not problems and bundle_total != payload["estimated_total_cents"]:
        problems.append(
            f"estimated_total_cents ({payload['estimated_total_cents']}) "
            f"!= sum(bundle.estimated_cents) ({bundle_total})"
        )
    return problems


def is_valid_offer(payload: Any) -> bool:
    return not validate_offer(payload)
