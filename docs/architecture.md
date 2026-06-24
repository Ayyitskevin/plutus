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
| `app.main` | FastAPI app shell (lifespan, middleware, static) |
| `app.routes.*` | HTTP routers — health, API, webhooks, storefront, SaaS UI |

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

## Production wiring (scripts — apply when credentials ready)

| Script | Purpose |
|--------|---------|
| `wire-mise-saas.sh` | Mise gallery index + media root for SaaS |
| `wire-dionysus-saas.sh` | Dionysus pitch enrichment on :8031 |
| `wire-r2.sh` / `wire-s3.sh` | Tenant gallery storage |
| `wire-whcc.sh` | WHCC lab adapter |
| `wire-smtp.sh` | SMTP order + client confirmation emails |
| `wire-public-url.sh` | `PLUTUS_SAAS_PUBLIC_URL` (+ optional Tailscale serve) |
| `wire-tier7.sh` / `wire-tier8.sh` | Orchestrators (Tier 8 defaults to GitHub-only tests) |

## Tier 9 (GitHub)

- Signup email verification (`PLUTUS_SIGNUP_VERIFY_EMAIL` + SMTP); pending keys stored as `key_id` (hashed), re-issued on verify
- Resend verification (`POST /ui/saas/resend-verification`, rate-limited; UI on pending + login pages)
- Admin-created tenants auto-marked `email_verified_at` so API keys work when verify is on
- Tenant notify-email on dashboard (`POST /ui/saas/app/settings`)
- Mise publish hook (`POST /webhooks/mise/gallery-published`, `PLUTUS_MISE_HOOK_TOKEN` — separate from admin token)
- Cloudflare tunnel templates (`ops/cloudflare-tunnel.example.yml`, `wire-cloudflare-tunnel.sh`)

## Tier 10 (GitHub)

- Stripe webhook process-then-dedup (500 on failure for Stripe retry)
- `PLUTUS_TENANT_KEY_PEPPER` fail-closed at SaaS startup
- Upload worker atomic claim + stale `analyzing` reaper
- Opaque UI sessions (`plutus_sid` cookie) — no raw API keys in cookies
- Signup defers API key until email verify
- DB indexes (upload status, stripe customer, orders)
- Login rate limit (5/min), `scripts/validate-env.sh`
- Postgres job in CI; graceful shutdown (`TimeoutStopSec=30`)
- Router split (`app/routes/*`) — thin `main.py`, grouped routers
- CSRF on cookie-session UI mutations (`app/routes/saas_mutations.py`, `Depends(require_csrf)`)

## Tier 11 (GitHub)

- Atomic `mark_order_paid_if_pending` — concurrent Stripe webhooks cannot double-count revenue
- Stripe webhook dedup records all handled event types (not only invoice failures)
- UI sessions invalidated when API key revoked or tenant deactivated
- Upload worker skips re-analyze when batch already has `run_id`
- Streaming multipart uploads (`uploads.add_upload_files` → `storage.save_gallery_stream`, 1MB chunks)
- S3 multipart transfer (`boto3` `TransferConfig`, 8MB threshold) for large gallery originals
- Dogfood scripts use `scripts/dogfood-session.sh` (`plutus_sid` + CSRF, not raw API key cookies)
- Postgres CI job (`.github/workflows/test.yml` → `scripts/ci-postgres.sh`)
- Legacy `plutus_ui_token` cookie auth removed — UI uses `plutus_sid` sessions only
- SaaS startup requires `PLUTUS_REDIS_URL` when rate limiting enabled (multi-worker safe)
- Mise publish webhook ignores form `tenant_id` in SaaS — scoped to `PLUTUS_MISE_HOOK_TENANT_ID`

## Tier 12 (GitHub)

- WHCC webhook HMAC-SHA256 (`X-WHCC-Signature: sha256=<hex>`; legacy Bearer still works for stub)
- Async UI mutations offload blocking DB/Argus/sell work via `asyncio.to_thread` (`app/async_io.py`)
- Postgres CI job in `.github/workflows/test.yml` (`test-postgres` → `scripts/ci-postgres.sh`)

## Tier 13 (GitHub)

- Full async offload: webhooks (Stripe/WHCC/Mise), API upload-batch, billing/share/mise UI routes
- Shared `app/redis_client.py` — health check, fail-closed SaaS rate limits (503, no memory fallback)
- `pip install -e '.[saas]'` bundles redis + postgres + boto3
- Upload worker Redis lock for SQLite multi-worker; Postgres uses `SKIP LOCKED`
- Audit log retention worker (`app/audit_retention.py`, hourly purge)
- Prometheus gauges: `upload_batches_queued`, `upload_batches_analyzing`, `rate_limit_exceeded` counter
- `scripts/dogfood-wait-batch.sh` — shared async-analyze polling for all dogfood scripts
- `wire-cloudflare-tunnel.sh --bootstrap` — optional tunnel create + DNS route

## Tier 14 (GitHub + fleet)

- `scripts/wire-redis.sh` — Docker Redis for SaaS rate limits (`plutus-redis` on :6379)
- Mise publish hook armed for `flow-studio` tenant (`PLUTUS_MISE_HOOK_*`)
- Remaining API routes async (`/analyze-folder`, `/recommend/mise-gallery`)
- CI installs `.[dev,saas]`; Postgres workflow job on GitHub Actions

## Tier 15 (fleet)

- `scripts/sync-flow-mise-plutus.sh` — push `MISE_PLUTUS_*` from plutus `.env` → flow `/opt/mise/.env`
- `wire-mise-hook-saas.sh` auto-runs sync after arming hook token
- Flow publish → `POST /webhooks/mise/gallery-published` on `:8031` (`MISE_PLUTUS_USE_WEBHOOK=true`)

## Tier 20 (photographer order ops)

- Order detail shows bundle hero, photo thumbnails, client track link with copy button
- `POST /ui/saas/app/orders/{id}/poll-lab` — refresh mock/WHCC fulfillment status
- `POST /ui/saas/app/orders/{id}/resend-confirmation` — re-send client confirmation when SMTP armed
- Token-scoped order photos: `GET /ui/saas/app/orders/{id}/photo/{filename}`
- Dashboard orders table shows client name and bundle title

## Tier 19 (SMTP notifications)

- Richer client confirmation email — bundle title, line items, total, tracking link
- Dashboard SMTP status + `POST /ui/saas/app/notifications/test` (sends to tenant notify email)
- `scripts/wire-smtp.sh` arms `PLUTUS_SMTP_*` in fleet `.env`
- `scripts/dogfood-smtp.sh` — local SMTP catcher validates photographer + client delivery

## Tier 18 (bundle editor)

- `/runs/{id}/edit` — tune title, pitch, photo per line item, enable/disable bundles
- `app/bundle_editor.py` persists edits to `recommendation_runs.payload_json`
- Disabled bundles omitted from storefront offers; checkout indices stay stable

## Tier 17 (client offer UX)

- Offer pages show bundle hero + per-item photo thumbnails (`store_offer.html`)
- Token-scoped photo route: `GET /store/{slug}/offer/{token}/photo/{filename}` (`app/gallery_media.py`)
- JPEG thumbs cached under `DATA_DIR/offer_thumbs`; S3 originals materialized on demand

## Tier 16 (fleet — public URL)

- `scripts/wire-cloudflare-flow.sh` — `plutus.kleephotography.com` via flow's `mise` tunnel → `strix-halo-a9-mega:8031`
- DNS CNAME routed; one-time `sudo cp` + `systemctl restart cloudflared` on flow activates ingress

## M2 — operator-led onboarding (GitHub)

- `PLUTUS_SIGNUP_ENABLED` defaults false — public signup closed; invite via admin UI
- Admin create tenant accepts **notify email**; sends welcome email with bootstrap API key when SMTP armed
- `notifications.send_tenant_welcome_email` — login, dashboard, storefront, getting-started steps

## M5 — deploy packaging + integration offer API (GitHub)

- `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `fly.toml`, `scripts/deploy-fly.sh`
- `POST /integrations/offer` — one-shot offer mint (admin + `tenant_id` or tenant key)
- `storefront.link_tenant_for_bearer` — shared tenant resolution for share-links + integrations
- mnemosyne `plutus_api` calls `/integrations/offer`

## M4 — trust surface + invite polish (GitHub)

- `/privacy` + `/terms` — public trust pages; linked from SaaS landing and login
- Admin tenant **invite kit** (copy-paste) + **resend welcome email** when SMTP armed
- `PLUTUS_MNEMOSYNE_URL` — album-bundle cross-sell CTA on client offer pages
- Dogfood scripts use `dogfood_bootstrap_tenant` (signup closed)

## M3 — mnemosyne / integration share-links (GitHub)

- `POST /storefront/share-links` accepts optional `tenant_id` for **admin** Bearer (SaaS mode)
- Defaults to `PLUTUS_MISE_HOOK_TENANT_ID` when `tenant_id` omitted — mnemosyne sets `MNEMOSYNE_PLUTUS_TENANT_ID`
- `scripts/dogfood-mnemosyne-link.sh` — admin-token offer mint E2E
- `scripts/dogfood-create-tenant.sh` — dogfood tenant + API key without public signup
- `scripts/dogfood-welcome-email.sh` — welcome email via local SMTP catcher

## Not built yet

- WHCC live API credentials on production fleet (HMAC + stub path ready; `scripts/wire-whcc.sh`)