# Plutus architecture

Plutus recommends physical products from an analyzed photo gallery. It does not render
prints or ship boxes — it produces **bundles** (SKU + photo mapping + rationale + cents).

## Flow

```text
folder / Argus signals → ingest → recommend (mock) → SQLite → review UI / JSON
```

## Modules

| Module | Role |
|--------|------|
| `app.ingest` | List images, read dimensions, optional Argus export merge |
| `app.catalog` | Static SKU catalog (Phase 0) |
| `app.recommend` | Mock bundle builder |
| `app.service` | Orchestration + persistence |
| `app.db` | SQLite runs |
| `app.main` | FastAPI routes + templates |

## Phase 1+ (not built)

- Mise webhook on gallery publish → auto-recommend
- Stripe Payment Link per bundle
- Lab adapter interface (WHCC, Miller's, etc.)
- LLM copy layer for client-facing pitch text (optional Dionysus handoff)