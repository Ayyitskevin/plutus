#!/usr/bin/env python3
"""Create Stripe product + price for Plutus SaaS tenant subscriptions."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=False)


def _upsert_env(path: Path, key: str, value: str) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out).rstrip() + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stripe setup for Plutus SaaS")
    parser.add_argument("--write-env", action="store_true", help="Write STRIPE_PRICE_ID to .env")
    parser.add_argument("--amount", type=int, default=2900, help="Monthly price in cents")
    args = parser.parse_args()

    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key or "CHANGE" in key.upper() or key == "sk_test_dogfood_local":
        print("Set STRIPE_SECRET_KEY in .env first (sk_test_ recommended).", file=sys.stderr)
        print("Dashboard: https://dashboard.stripe.com/test/apikeys", file=sys.stderr)
        return 1

    from app.billing import _stripe_request, stripe_test_mode

    if not stripe_test_mode():
        print("Warning: using live Stripe key — prices will be live-mode.", file=sys.stderr)

    product = _stripe_request(
        "POST",
        "/products",
        {
            "name": "Plutus Pro",
            "description": "Print & album upsell for client galleries",
            "metadata[plutus_plan]": "pro",
        },
    )
    price = _stripe_request(
        "POST",
        "/prices",
        {
            "product": product["id"],
            "unit_amount": str(args.amount),
            "currency": "usd",
            "recurring[interval]": "month",
            "metadata[plutus_plan]": "pro",
        },
    )
    price_id = price["id"]
    print(f"product_id={product['id']}")
    print(f"price_id={price_id}")
    print(f"test_mode={stripe_test_mode()}")

    if args.write_env:
        env_path = ROOT / ".env"
        _upsert_env(env_path, "STRIPE_PRICE_ID", price_id)
        print(f"wrote STRIPE_PRICE_ID to {env_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())