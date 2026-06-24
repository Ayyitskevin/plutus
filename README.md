# plutus

AI print & album sales upsell layer for photo galleries.

Plutus turns a delivered gallery (plus optional [Argus](https://github.com/Ayyitskevin/argus)
vision signals) into client-ready print bundles — canvas, fine art prints, albums,
and gift sets with SKU pairings and price estimates. SaaS tenants get a branded
storefront, Stripe checkout, and lab order handoff.

Part of the Kevin Lee photography suite alongside [mise](https://github.com/Ayyitskevin/mise),
[argus](https://github.com/Ayyitskevin/argus), [mnemosyne](https://github.com/Ayyitskevin/mnemosyne),
and [dionysus](https://github.com/Ayyitskevin/dionysus).

## What's built (Phases 0–9)

| Mode | Port | Highlights |
|------|------|------------|
| **Homelab** | `8030` | Folder analyze, Mise gallery hook, mock/vision recommend, pitch export |
| **SaaS** | `8031` | Multi-tenant auth, admin-invite onboarding (+ optional self-signup), uploads (local/S3), async Argus vision, share links, Stripe checkout, lab mock/WHCC stub, Mise hook |

**SaaS flow:** admin creates tenant (welcome email with API key) → upload gallery → Argus auto-vision (background) → vision-aware bundles → share offer link → client Stripe checkout → order + lab poll. Set `PLUTUS_SIGNUP_ENABLED=true` to open public self-service signup.

## Quickstart — homelab

```bash
cd ~/ai-workspace/plutus
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.homelab.example .env

.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8030
```

Open http://127.0.0.1:8030 and point at a gallery folder, or:

```bash
curl -s -X POST http://127.0.0.1:8030/analyze-folder \
  -d "folder=/path/to/gallery&name=Demo&limit=20"
```

## Quickstart — SaaS

```bash
cd ~/ai-workspace/plutus
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.saas.example .env
# Edit: PLUTUS_API_TOKEN, PLUTUS_TENANT_KEY_PEPPER, PLUTUS_ARGUS_* , STRIPE_*

bash scripts/start-plutus-saas.sh
```

Open http://127.0.0.1:8031 — sign up for a trial, upload a gallery, create a share link.

**Production service (user systemd):**

```bash
bash scripts/install-saas-service.sh
journalctl --user -u plutus-saas -f
```

## Key API routes

| Route | Auth | Purpose |
|-------|------|---------|
| `POST /analyze-folder` | — | Homelab folder → bundles |
| `POST /recommend/mise-gallery` | Bearer | Mise publish hook |
| `POST /recommend/upload-batch` | Bearer | Tenant upload → analyze (202 async) |
| `GET /upload-batches/{id}/status` | Bearer | Poll async analyze |
| `POST /storefront/share-links` | Bearer | Client offer URL (tenant key, or admin + `tenant_id`) |
| `POST /store/{slug}/offer/{token}/checkout` | — | Stripe client checkout |
| `POST /webhooks/stripe` | Stripe sig | Payment → order |
| `POST /webhooks/mise/gallery-published` | Hook token | Mise publish → recommend |
| `POST /ui/saas/resend-verification` | — | Resend signup verify email |
| `GET /healthz` | — | DB, Argus, Stripe, storage, lab checks |

Tenant auth: `Authorization: Bearer plutus_tk_<tenant>_<token>`

## Dogfood scripts

```bash
bash scripts/dogfood-phase5.sh      # upload → Argus grok → recommend
bash scripts/dogfood-phase6.sh      # share → simulate pay → lab
bash scripts/dogfood-stripe-real.sh # real Stripe checkout + signed webhook
bash scripts/stripe-listen.sh       # forward webhooks locally (needs Stripe CLI)
```

## Configuration

See `.env.homelab.example` (homelab) and `.env.saas.example` (cloud). Common variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `PLUTUS_SAAS_MODE` | `false` | Enable multi-tenant SaaS |
| `PLUTUS_PORT` | `8030` / `8031` | Bind port |
| `PLUTUS_DATA_DIR` | `./data` | SQLite + uploads |
| `PLUTUS_ARGUS_URL` | — | Argus base URL |
| `PLUTUS_ARGUS_AUTO_VISION` | `false` | Auto-run vision on upload analyze |
| `PLUTUS_UPLOAD_ASYNC_ANALYZE` | `true` | Background upload worker |
| `STRIPE_SECRET_KEY` | — | Tenant billing + client checkout |
| `STRIPE_WEBHOOK_SECRET` | — | Signed webhook verification |
| `PLUTUS_SIGNUP_VERIFY_EMAIL` | `false` | Require email confirm when SMTP set |
| `PLUTUS_MISE_HOOK_TOKEN` | — | Mise publish webhook (not admin token) |

## Tests

```bash
.venv/bin/pytest tests/ -q
.venv/bin/ruff check app tests
```

## Docs

- [docs/PHASE-0.md](docs/PHASE-0.md) — scope and magic moment
- [docs/architecture.md](docs/architecture.md) — module map and data flow