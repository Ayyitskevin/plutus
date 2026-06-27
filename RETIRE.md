# RETIRE.md — what Mise owns, what Plutus can turn off

Plutus is the **offers worker** for [Mise](https://github.com/Ayyitskevin/mise): it
proposes print/album bundles for a delivered gallery. It is a **stateless,
reproducible, contract-true recommendation worker** — never a second source of
truth, never a SaaS. This document records what is authoritative (Mise) versus
disposable (Plutus), so Plutus's own DB / UI / auth can be retired without data
loss.

## Mise owns all business state

Plutus must never charge, send to a client, or create an invoice. Offers are
**proposals a human reviews and applies in Mise**. The following live only in
Mise and Plutus must not reproduce them:

- Clients, galleries, invoices, payments
- Operator identity / accounts
- The offer review → approve → send workflow
- Final pricing and settlement (Plutus `*_cents` are estimates/proposals only)
- Any signup, tenant, subscription, Stripe/checkout, or storefront surface

## What Plutus keeps (and why it's disposable)

| Surface | Purpose | Authoritative? | Reproducible from |
|---|---|---|---|
| `galleries`, `recommendation_runs` tables | recommendation run cache | No | Mise gallery originals + optional Argus run → deterministic bundles |
| `/runs/{id}`, `/runs/{id}/pitch.txt` UI | operator review convenience | No | the run cache above |
| Service-token register (auth) | guards the Mise recommend path | No (shared secret w/ Mise) | re-issue from Mise's `MISE_PLUTUS_TOKEN` |
| `/healthz` | liveness/deps probe | No | — |

Because every offer is a deterministic function of the gallery originals and the
Argus vision run (both owned upstream), the Plutus database is a **cache**: it can
be dropped and rebuilt by re-running `/recommend/mise-gallery`. No Plutus row is a
system of record.

## The contract surface that must stay (only while Plutus runs)

If Plutus is kept, these are the contract with Mise (see `app/offer_schema.py`):

- `POST /recommend/mise-gallery` → strict offer JSON: `run_id`,
  `estimated_total_cents`, `offer_url`, `pitch_url`, `bundles[]` each with a stable
  `sku` + `line_items[{sku,label,qty,unit_cents}]`, plus provenance
  (`model`, `latency_ms`, `cost_usd`). The bundle `sku` + line-item catalog `sku`
  let an accepted offer link to a Mise invoice line for real upsell attribution.
- **Idempotency:** one stable offer per gallery — a re-run refreshes the same
  `run_id` in place; it never creates a second offer or duplicate bundles.
- **`correlation_id`** is echoed back unchanged when Mise sends it.
- The **service-token register** (`PLUTUS_API_TOKEN`, `PLUTUS_MISE_HOOK_TOKEN`,
  `PLUTUS_SERVICE_TOKENS`) is part of the contract — keep it in sync with Mise's
  `MISE_PLUTUS_TOKEN`.

## What is already turned off in studio mode

- `SAAS_MODE = False` (hardcoded). SaaS portal / signup / billing / webhooks
  routers are **not registered**.
- `PLUTUS_HOMELAB_STORE_ENABLED=false` by default.
- The reachable homelab **storefront / order / lab-fulfillment routes have been
  removed** — Plutus no longer exposes any client checkout, order, or fulfillment
  surface.

## How to retire Plutus entirely

1. Point Mise's recommend call away from Plutus (or stop calling it). Publishing
   in Mise must already tolerate a Plutus failure as a swallowed status — a Plutus
   outage never blocks Mise's publish path.
2. Drop the Plutus database. Nothing needs migrating: accepted offers and their
   invoice lines already live in Mise, and any past recommendation can be
   regenerated from the gallery originals + Argus run.
3. Decommission the Plutus service, UI, and tokens.

## SaaS surface — removed

The SaaS/money/identity modules, their templates, and their database tables have
been deleted. `migrate()` creates the recommendation run cache (`galleries`,
`recommendation_runs`) plus `callback_deadletter` — an **operational outbox** for
offer callbacks that exhausted retries (re-deliverable, disposable; each row's
offer is reproducible from its run, so it is not authoritative state). There is no
`tenants`, `orders`, `storefront_tokens`, `stripe_webhook_events`, `upload_batches`,
… table, and no signup/tenant/subscription/Stripe/storefront/order/lab code in the
tree. Plutus holds no authoritative business state.
