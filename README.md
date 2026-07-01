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

## Sources

| Source     | Routed by                     | Notes                                                      |
| ---------- | ----------------------------- | ---------------------------------------------------------- |
| ATS        | `.json`                       | Vendor-agnostic field aliasing.                            |
| Resume     | `.txt`, `.pdf`, `.docx`       | Section + prose extraction strategies.                     |
| **GitHub** | `.github` file (or UI/CLI/API)| Content is a profile URL (or bare username); fetched live. |

The GitHub source takes a **profile URL** (e.g. `https://github.com/octocat`; a
bare `octocat`/`@octocat` handle also works), resolves it to a username, calls
`GET https://api.github.com/users/{username}`, and maps the public profile (name,
email, bio, location, blog/Twitter/profile links, company) into the same canonical
claims, so it resolves and fuses alongside every other source. A URL that 404s,
rate-limits, or is malformed is quarantined like any other bad source - it never
crashes the run. The URL can be supplied three ways: a `.github` file in
`--inputs`, the `--github` CLI flag, or the **GitHub profile URLs** field in the UI
/ `github` form field of the API.

## Web UI

Start the server and open the self-contained test UI (no build step, works offline):

```bash
uvicorn app.api.main:app --reload
# open http://127.0.0.1:8000/
```

On startup the server downloads and loads the GLiNER NER model (one-time per
machine unless cached). The first transform with a résumé then runs without a
Hugging Face fetch.

Select one or more source files (try `samples/ats_sample.json`,
`samples/resume_sample.txt`, and `samples/broken_source.json` together), optionally
load the **Custom example** config, and click **Transform**. The result shows a
summary banner, prominently highlighted quarantined sources, the canonical profile
(with `null`/withheld fields highlighted), the projected output, and any violations.

## API

- `GET /health` &rarr; `{"status":"ok"}`
- `GET /` &rarr; the test UI
- `POST /transform` (multipart/form-data): `files` (zero or more uploaded sources),
  an optional `github` field (comma/space/newline separated usernames or profile
  URLs), and an optional `config` JSON string field (empty &rarr; full default
  projection). Returns profiles, the config projection, quarantined sources,
  validation reports, and a summary. A bad **source** is returned quarantined (still
  HTTP 200); only an invalid **config** is a 422.

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

## CLI

Runs the same pipeline and emits the identical response JSON as the API:

```bash
python -m app.cli --inputs samples/ats_sample.json samples/resume_sample.txt \
  --github https://github.com/octocat \
  --config samples/config_custom.json --out result.json
# provide --inputs and/or --github (at least one source); omit --config
# (or pass "none") for the full canonical schema; omit --out for stdout
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
samples/         # ats_sample.json, resume_sample.txt, github_octocat.github, broken_source.json, config_custom.json
tests/           # pytest suite
```
