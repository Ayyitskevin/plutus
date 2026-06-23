# plutus

AI print & album sales upsell layer for photo galleries.

Plutus turns a delivered gallery (plus optional [Argus](https://github.com/Ayyitskevin/argus)
keeper/hero signals) into client-ready print bundles — canvas, fine art prints, albums,
and gift sets with SKU pairings and price estimates.

Part of the Kevin Lee photography suite alongside [mise](https://github.com/Ayyitskevin/mise),
[argus](https://github.com/Ayyitskevin/argus), [mnemosyne](https://github.com/Ayyitskevin/mnemosyne),
and [dionysus](https://github.com/Ayyitskevin/dionysus).

## Phase 0 (current)

- Analyze a local gallery folder → upsell bundle recommendations
- Optional Argus run id to enrich with culling scores
- Web review UI + JSON export
- Mock engine (no checkout, no lab APIs yet)

## Quickstart

```bash
cd ~/ai-workspace/plutus
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env

.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8030
```

Open http://127.0.0.1:8030 and point at a gallery folder, or:

```bash
curl -s -X POST http://127.0.0.1:8030/analyze-folder \
  -d "folder=/path/to/gallery&name=Demo&limit=20"
```

With Argus signals (homelab):

```bash
# .env: PLUTUS_ARGUS_URL=http://strix-halo-a9-mega:8010
#       PLUTUS_ARGUS_TOKEN=<shared bearer>
curl -s -X POST http://127.0.0.1:8030/analyze-folder \
  -d "folder=/path/to/gallery&argus_run_id=199"
```

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `PLUTUS_HOST` | `0.0.0.0` | Bind host |
| `PLUTUS_PORT` | `8030` | Bind port |
| `PLUTUS_DATA_DIR` | `./data` | SQLite + local data |
| `PLUTUS_ARGUS_URL` | — | Argus base URL for export merge |
| `PLUTUS_ARGUS_TOKEN` | — | Bearer for Argus export |

## Tests

```bash
.venv/bin/pytest tests/ -q
.venv/bin/ruff check app tests
```

## Docs

- [docs/PHASE-0.md](docs/PHASE-0.md) — scope and magic moment
- [docs/architecture.md](docs/architecture.md) — module map and roadmap