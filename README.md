# Multi-Source Candidate Data Transformer

Merge messy, multi-source candidate data into one clean, provenanced,
confidence-scored canonical profile, with a config-driven projection layer and a
FastAPI surface.

## Design spine

- Every extracted value is a **claim**, not a fact: it records who said it and how
  it was obtained.
- The canonical profile is an **adjudicated verdict** over claims.
- The system stays **honestly-empty** rather than confidently-wrong.
- A hard wall separates the **canonical record** from a **config-driven
  projection** (config is data the engine interprets, never a code change).
- Architecture is an **hourglass**: many sources fan in to one `Claim` type, then
  one fused record fans out to many views.

## Status

Step 0: project scaffold and tooling only. No business logic yet (normalize,
fuse, project, validate land in later steps).

## Requirements

- Python 3.11+

## Setup

```bash
python -m venv .venv
# Windows (PowerShell): .venv\Scripts\Activate.ps1
# macOS/Linux:          source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Alternatively, install just the runtime dependencies from `requirements.txt`:

```bash
python -m pip install -r requirements.txt
```

## Common tasks

With `make` (macOS/Linux, or Windows with `make` installed):

```bash
make install     # editable install with dev deps
make lint        # ruff check .
make typecheck   # mypy app  (runs in --strict mode via pyproject)
make test        # pytest
make run         # uvicorn app.api.main:app --reload
```

Without `make` (e.g. plain Windows PowerShell), run the underlying commands
directly:

```bash
ruff check .
mypy app
pytest
uvicorn app.api.main:app --reload
```

## Health check

Once the server is running:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

## Layout

```
app/
  domain/        # models, enums
  normalize/     # registry + normalizers
  sources/       # adapters + detection
  pipeline/      # ledger, resolve, fuse, project, validate, orchestrate
  api/           # FastAPI app (GET /health)
  cli.py         # thin entrypoint (stub in Step 0)
samples/         # ats_sample.json, resume_sample.txt, broken_source.json
tests/           # pytest suite
```

## Definition of done (Step 0)

- `make lint`, `make typecheck`, `make test` run clean.
- `GET /health` returns `{"status":"ok"}`.
