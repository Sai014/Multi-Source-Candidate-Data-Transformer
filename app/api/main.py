"""FastAPI application surface.

Exposes a health check, a thin ``POST /transform`` endpoint, and a self-contained
test UI at ``GET /``. The route layer is deliberately thin: it handles temp-file IO
and config parsing, then defers all transformation to the pure core via
``orchestrate.run``. A bad *source* is never an error - it comes back quarantined;
only an invalid *config* is the caller's fault (HTTP 422).
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ValidationError

from app.api.schemas import TransformResponse, build_transform_response
from app.domain.models import Config
from app.pipeline.orchestrate import run
from app.sources.github import handle_to_stem

app = FastAPI(
    title="Multi-Source Candidate Data Transformer",
    version="0.1.0",
)

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_INDEX_HTML = _STATIC_DIR / "index.html"


class HealthResponse(BaseModel):
    """Response model for the health check endpoint."""

    status: Literal["ok"]


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Report service liveness."""
    return HealthResponse(status="ok")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Serve the self-contained test UI."""
    return FileResponse(_INDEX_HTML, media_type="text/html")


def _parse_config(raw: str | None) -> Config:
    """Parse a config form field; an absent/empty value means the default projection.

    A malformed JSON string or a value that fails schema validation is the caller's
    fault and is surfaced as HTTP 422.
    """
    if raw is None or not raw.strip():
        return Config()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid config JSON: {exc}") from exc
    try:
        return Config.model_validate(parsed)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid config: {exc}") from exc


def _write_temp(upload: UploadFile, data: bytes, tmp_dir: Path, index: int) -> Path:
    """Write one upload into a per-index temp subdir, keeping its original filename.

    Each upload gets its own subdirectory so identical filenames never collide while
    the basename (and the routing suffix) are preserved for adapters and the UI.
    """
    name = Path(upload.filename).name if upload.filename else f"source_{index}"
    sub_dir = tmp_dir / str(index)
    sub_dir.mkdir(parents=True, exist_ok=True)
    path = sub_dir / name
    path.write_bytes(data)
    return path


def _split_handles(raw: str | None) -> list[str]:
    """Split a free-form GitHub field (comma/space/newline separated) into tokens."""
    if raw is None:
        return []
    return [token for token in re.split(r"[\s,]+", raw.strip()) if token]


def _stage_github(handles: list[str], tmp_dir: Path, start_index: int) -> list[Path]:
    """Write each GitHub URL/handle to its own ``.github`` source file for routing."""
    paths: list[Path] = []
    for offset, handle in enumerate(handles):
        index = start_index + offset
        sub_dir = tmp_dir / f"gh_{index}"
        sub_dir.mkdir(parents=True, exist_ok=True)
        path = sub_dir / f"{handle_to_stem(handle, f'github_{index}')}.github"
        path.write_text(handle, encoding="utf-8")
        paths.append(path)
    return paths


@app.post("/transform", response_model=TransformResponse)
async def transform(
    files: list[UploadFile] = File(default_factory=list),
    github: str | None = Form(default=None),
    config: str | None = Form(default=None),
) -> TransformResponse:
    """Transform uploaded sources and/or GitHub profile URLs into a canonical profile.

    ``github`` is an optional comma/space/newline separated list of GitHub profile
    URLs (e.g. ``https://github.com/octocat``); a bare username is also accepted.
    Each is resolved to a username and fetched from the GitHub users API, then fed
    through the same pipeline. The route stages every source to a temp file, runs
    the pure pipeline, and always cleans up. Garbage sources never fail the request;
    they come back inside ``quarantined``.
    """
    parsed_config = _parse_config(config)
    handles = _split_handles(github)

    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        paths: list[Path] = []
        for index, upload in enumerate(files):
            data = await upload.read()
            paths.append(_write_temp(upload, data, tmp_dir, index))
        paths.extend(_stage_github(handles, tmp_dir, len(paths)))
        result = run(paths, parsed_config)

    return build_transform_response(result)
