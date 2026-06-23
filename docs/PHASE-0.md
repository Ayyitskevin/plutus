# plutus — Phase 0 scope

**One-line promise:** point plutus at a client gallery folder (optionally enriched with
Argus keeper/hero scores) and get *actionable* print & album upsell bundles — specific
SKUs, photo pairings, and dollar estimates a photographer would actually send to a client.

Phase 0 is **not** checkout, lab fulfillment, or Mise embedding. It proves the recommendation
logic feels worth sending before we wire Stripe, WHCC/Millers APIs, or gallery iframes.

## IN

- Ingest local gallery folder (JPEG/PNG/WebP/HEIC).
- Optional overlay from Argus run export (`PLUTUS_ARGUS_URL` + run id).
- Static product catalog (prints, canvas, metal, albums, gift sets).
- Mock recommendation engine mapping top frames → bundles.
- SQLite persistence (`galleries`, `recommendation_runs`).
- FastAPI + Jinja UI: analyze form, bundle review, JSON export.
- `POST /analyze-folder` for scripting.

## Phase 1 (shipped)

- `POST /recommend/mise-gallery` — Mise POSTs `mise_gallery_id` after Argus analyze
- `PLUTUS_MISE_MEDIA_ROOT` + `scripts/sync-mise-media.sh` for homelab media
- Client pitch export (`/runs/{id}/pitch.txt`) for copy-paste emails
- Mise admin tile: Plutus status, bundle link, pitch link

## OUT

- Stripe checkout / payment capture.
- Print-lab API submission.
- Mise gallery iframe or PIN-gated client store.
- Multi-tenant auth.
- AI-generated marketing copy (Dionysus owns narrative; Plutus owns SKUs).

## Magic moment

Open `/runs/{id}` after analyzing a real F&B gallery and think: *"I'd actually paste
these bundles into a client email."*

## Suite position

```text
mise gallery → argus vision → plutus upsell → (future) client checkout
mnemosyne album design ← cross-sell from plutus album bundles
```

## Port

Homelab default: **8030** (`PLUTUS_PORT`).