# Plutus architecture

Plutus recommends physical products from an analyzed photo gallery. It does not render
prints or ship boxes — it produces **bundles** (SKU + photo mapping + rationale + cents),
then (in SaaS mode) sells them via a branded storefront and hands paid orders to a lab adapter.

## Modes

| Mode | `PLUTUS_SAAS_MODE` | Typical port |
|------|-------------------|--------------|
| Homelab | `false` | 8030 |
| SaaS cloud | `true` | 8031 |

## Data flow

### Homelab

```text
folder / Argus export → ingest → recommend (mock|vision) → SQLite → review UI / pitch.txt
Mise gallery publish → POST /recommend/mise-gallery → same pipeline
```

### SaaS

```text
signup → tenant API key
upload batch → storage (local|S3) → async worker
  → Argus auto-vision (optional) → ingest → vision-aware recommend → run
share link → client storefront → Stripe checkout → webhook → order → lab adapter
```

## Modules

| Module | Role |
|--------|------|
| `app.ingest` | List images, dimensions, Argus export merge |
| `app.argus_client` | Auto-vision jobs, health probe |
| `app.catalog` | SKU catalog + per-tenant pricing overrides |
| `app.recommend` | Vision-aware bundle builder (food/wedding themes) |
| `app.service` | Orchestration, upload batch analyze, enqueue |
| `app.upload_worker` | Background thread — queued → analyzing → analyzed/failed |
| `app.uploads` / `app.storage` | Tenant gallery batches, local disk or S3 |
| `app.db` | SQLite — galleries, runs, tenants, orders, upload_batches |
| `app.auth` / `app.tenants` | Bearer API keys, admin token, signup |
| `app.metering` | Trial caps, monthly recommend limits |
| `app.storefront` | Share links, offer resolution |
| `app.orders` / `app.billing` | Stripe checkout, webhooks, simulate pay (dogfood) |
| `app.lab` / `app.lab_whcc` | Mock lab poll or WHCC stub |
| `app.health` | `/healthz` dependency checks |
| `app.main` | FastAPI routes + Jinja templates |

## Upload batch states

```text
ready → queued → analyzing → analyzed
                          └→ failed (retry via POST analyze)
```

## Vision recommend

When Argus returns keeper scores, shot types, or keywords, `recommend.recommend_bundles`
switches `engine` to `vision` and picks a `gallery_theme` (`food`, `wedding`, `general`).
Bundles adapt — e.g. metal accent prints for food detail shots, wedding album pairings.

## Ops

- Homelab: `ops/plutus-user.service` + `scripts/install-user-service.sh` (port 8030)
- SaaS: `ops/plutus-saas-user.service` + `scripts/install-saas-service.sh` (port 8031)
- Metrics: Prometheus at `/metrics` when `PLUTUS_PROMETHEUS_ENABLED=true`
- Structured logs when `PLUTUS_STRUCTURED_LOGS=true`

## Not built yet

- Signup email verification
- Production S3 + real WHCC fulfillment
- Mise auto-recommend on homelab dogfood (deferred)
- LLM pitch copy layer (optional Dionysus handoff)