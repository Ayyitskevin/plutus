# CLAUDE.md — Plutus

Bootstrap for every Claude Code session in this repo. Read this first.

## What Plutus is

Plutus is **Mise's OFFERS worker**. Given a delivered gallery (plus optional
[Argus](https://github.com/Ayyitskevin/argus) vision signals) it proposes
print/album **bundle recommendations** — canvas, fine-art prints, albums, gift
sets — with stable SKUs and price *estimates*.

Plutus **RECOMMENDS, nothing else.** It must **NEVER charge a card, send anything
to a client, or create/settle an invoice.** Mise owns all business state (clients,
galleries, invoices, payments, operator identity, and the offer
review → approve → send workflow). Plutus is a **stateless, reproducible,
contract-true** worker — never a second source of truth, never a SaaS.

The single most valuable thing Plutus emits: a **stable per-bundle `sku`** and
**`line_items`** that map **1:1 to the invoice lines a human later creates in
Mise**, so an accepted offer can be attributed to real upsell revenue.

## The 7-point worker contract (what "aligned" means)

1. **Structured output.** A recommendation is strict JSON — see
   `app/offer_schema.py` (the single source of truth; `to_mise_offer()` is the
   canonical wire shape, `validate_offer()` checks it):
   ```json
   {"run_id": 0, "estimated_total_cents": 0, "offer_url": "...", "pitch_url": "...",
    "model": "...", "latency_ms": 0, "cost_usd": 0.0,
    "bundles": [{"sku": "<stable id>", "label": "...", "estimated_cents": 0,
      "line_items": [{"sku": "<catalog id>", "label": "...", "qty": 1, "unit_cents": 0}]}]}
   ```
   The per-bundle `sku` + line-item catalog `sku` are the headline — they link an
   accepted offer to Mise invoice lines.
2. **Provenance + cost.** Every run reports `model`, `latency_ms`, `cost_usd`
   (Mise persists these to its ai_runs ledger / cost report).
3. **Callback contract.** Echo any `correlation_id` Mise sends. Results may also
   POST to Mise at `/api/plutus/callback?gallery_id=<id>` with a bearer service
   token (config-gated, default OFF — `app/mise_callback.py`). Delivery is
   hardened: a stable `Idempotency-Key` per `(gallery_id, run_id)` so re-delivery
   never duplicates; a 401 refreshes the token from `.env` and retries once;
   transient failures retry with backoff, then dead-letter to a local re-deliverable
   outbox. A callback for an unknown subject is a no-op, never an error, and a
   callback failure never crashes the recommend path.
4. **Idempotency.** **One stable offer per gallery** — a retry/re-run refreshes
   the same `run_id` in place; it never creates a second offer or duplicate
   bundles.
5. **Stateless / retire-ready.** The DB is a recommendation run cache
   (`galleries` + `recommendation_runs`) plus `callback_deadletter` (an operational,
   re-deliverable callback outbox — not business state); outputs are reproducible
   from the Mise gallery + Argus run. Plutus's DB/UI/auth could be retired with no
   data loss. See `RETIRE.md`.
6. **Auth robustness.** Inbound auth is one constant-time **service-token
   register** (`app/service_tokens.py`): `PLUTUS_API_TOKEN`,
   `PLUTUS_MISE_HOOK_TOKEN`, and any `PLUTUS_SERVICE_TOKENS` rotation tokens are
   all accepted. Keep it in sync with Mise's `MISE_PLUTUS_TOKEN`.
7. **Resilience & CI.** A Plutus failure must never crash Mise's
   publish/recommend path (failures are swallowed and recorded as status). Expose
   `/healthz`. CI is **mock-only / reproducible** (see below).

The live entry point is `POST /recommend/mise-gallery`
(`app/routes/api.py` → `mise_hook` → `service.analyze_mise_gallery` →
`recommend.recommend_bundles` + `_persist_run`). Don't break it.

## Money guardrails (non-negotiable)

- **Proposals only.** Every `*_cents` value is an estimate a human reviews and
  applies in Mise. A model never sets a final price or settlement.
- **No money surface.** There is no Stripe/checkout/billing/storefront/order/lab
  code in this repo — it was removed. Do not reintroduce it.
- **SKU → invoice line.** The stable bundle `sku` and per-line catalog `sku` exist
  so Mise can attribute an accepted offer to real invoice lines. Preserve them.

## Branch / PR convention

- Develop on **`claude/…` branches**. **Never push to `main`.**
- Ship every change as a **draft PR a human merges** — small, reviewable, and
  **each PR independently green** (lint + tests).
- Gate new behavior behind config with backward-compatible defaults; don't break
  the existing recommend/callback path.

## CI rule: mock-only / reproducible

- **No live model or external API calls in tests.** Mock Argus/Dionysus/Mise/HTTP;
  patch `httpx`. The recommend engine is deterministic, so outputs are stable
  enough to assert the schema.
- CI runs `ruff check app tests` then `pytest tests/` on SQLite **and** Postgres,
  plus a docker smoke build. Lint with the CI ruff version to avoid drift.
- Validate the offer shape with `app/offer_schema.py` rather than hand-rolled
  assertions.

## Local dev

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check app tests
PLUTUS_DATABASE_URL= .venv/bin/pytest tests/ -q
```
