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

## Web UI

Start the server and open the self-contained test UI (no build step, works offline):

```bash
uvicorn app.api.main:app --reload
# open http://127.0.0.1:8000/
```

Select one or more source files (try `samples/ats_sample.json`,
`samples/resume_sample.txt`, and `samples/broken_source.json` together), optionally
load the **Custom example** config, and click **Transform**. The result shows a
summary banner, prominently highlighted quarantined sources, the canonical profile
(with `null`/withheld fields highlighted), the projected output, and any violations.

## API

- `GET /health` &rarr; `{"status":"ok"}`
- `GET /` &rarr; the test UI
- `POST /transform` (multipart/form-data): `files` (one or more sources) and an
  optional `config` JSON string field (empty &rarr; full default projection).
  Returns profiles, the config projection, quarantined sources, validation reports,
  and a summary. A bad **source** is returned quarantined (still HTTP 200); only an
  invalid **config** is a 422.

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

## CLI

Runs the same pipeline and emits the identical response JSON as the API:

```bash
python -m app.cli --inputs samples/ats_sample.json samples/resume_sample.txt \
  --config samples/config_custom.json --out result.json
# omit --config (or pass "none") for the full canonical schema; omit --out for stdout
```

## Layout

```
app/
  domain/        # models, enums
  normalize/     # registry + normalizers
  sources/       # adapters + detection
  pipeline/      # ledger, resolve, fuse, project, validate, orchestrate
  api/           # FastAPI app (/health, /transform) + static test UI
  cli.py         # CLI entrypoint (same output as the API)
samples/         # ats_sample.json, resume_sample.txt, broken_source.json, config_custom.json
tests/           # pytest suite
```
