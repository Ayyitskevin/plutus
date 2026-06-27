# plutus

Print & album **upsell recommendations** for [Mise](https://github.com/Ayyitskevin/mise) gallery admin.

Plutus turns a delivered gallery (plus optional [Argus](https://github.com/Ayyitskevin/argus)
vision signals) into client-ready print bundles — canvas, fine art prints, albums,
and gift sets with SKU pairings and price estimates. You review bundles in the Plutus UI
and copy `pitch.txt` into a client email.

**Studio feature only** — no SaaS signup, Stripe checkout, or client payment flows.

Part of the Kevin Lee photography suite alongside [mise](https://github.com/Ayyitskevin/mise),
[argus](https://github.com/Ayyitskevin/argus), [mnemosyne](https://github.com/Ayyitskevin/mnemosyne),
and [dionysus](https://github.com/Ayyitskevin/dionysus).

## Mise admin flow

```text
Publish gallery in Mise → Argus vision (optional) → Plutus bundles
  → review at /runs/{id} → copy /runs/{id}/pitch.txt to client
```

Mise gallery admin shows **Print & album bundles** with links to review and pitch.

## Quickstart

```bash
cd ~/ai-workspace/plutus
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.homelab.example .env
# Edit PLUTUS_API_TOKEN (same as MISE_PLUTUS_TOKEN on flow)

.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8030
```

Wire Mise on flow:

```bash
# flow /opt/mise/.env
MISE_PLUTUS_URL=http://strix-halo-a9-mega:8030
MISE_PLUTUS_TOKEN=<same as PLUTUS_API_TOKEN>
```

## Key API routes

| Route | Auth | Purpose |
|-------|------|---------|
| `POST /recommend/mise-gallery` | Bearer `PLUTUS_API_TOKEN` | Mise → bundles + `review_url` + `pitch_url` |
| `POST /analyze-folder` | Bearer (optional) | Local folder → bundles |
| `GET /runs/{id}` | — | Review bundles (UI) |
| `GET /runs/{id}/pitch.txt` | — | Copy-paste client email |
| `GET /healthz` | — | DB, Mise, Argus checks |

## Dogfood

```bash
bash scripts/dogfood-suite-loop.sh   # Mise gallery → Argus → Plutus (when fleet wired)
```

## Configuration

See `.env.homelab.example`. Required for Mise integration:

- `PLUTUS_API_TOKEN` — shared secret with `MISE_PLUTUS_TOKEN` (the same value on both sides)
- `PLUTUS_SERVICE_TOKENS` — optional comma-separated extra tokens accepted on the
  Mise recommend path. All registered tokens are compared in constant time; set the
  NEW secret here during a rotation so publishing never 401s while you swap Mise over
- `PLUTUS_MISE_URL` + `PLUTUS_MISE_API_TOKEN` — gallery metadata
- `PLUTUS_MISE_MEDIA_ROOT` — synced originals (`scripts/sync-mise-media.sh`)
- `PLUTUS_ARGUS_URL` + `PLUTUS_ARGUS_TOKEN` — optional vision enrichment