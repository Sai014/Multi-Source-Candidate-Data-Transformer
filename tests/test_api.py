"""API surface tests: /transform shape, config 422, garbage-source quarantine.

Also asserts the CLI emits output identical to the API for the same inputs (paths
normalized to basenames, since the API stages uploads through temp files).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.api.main import app
from app.cli import transform_inputs

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
client = TestClient(app)

ATS = SAMPLES / "ats_sample.json"
RESUME = SAMPLES / "resume_sample.txt"
BROKEN = SAMPLES / "broken_source.json"
CONFIG = SAMPLES / "config_custom.json"


def _upload(path: Path, mime: str) -> tuple[str, tuple[str, bytes, str]]:
    return ("files", (path.name, path.read_bytes(), mime))


def _all_samples() -> list[tuple[str, tuple[str, bytes, str]]]:
    return [
        _upload(ATS, "application/json"),
        _upload(RESUME, "text/plain"),
        _upload(BROKEN, "application/json"),
    ]


def test_index_serves_ui() -> None:
    """GET / returns the self-contained HTML page."""
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "Transform" in res.text


def test_transform_shape_with_custom_config() -> None:
    """Samples + custom config: profiles present, one quarantined, emails[0] remapped."""
    res = client.post(
        "/transform",
        files=_all_samples(),
        data={"config": CONFIG.read_text(encoding="utf-8")},
    )
    assert res.status_code == 200
    body = res.json()

    assert len(body["profiles"]) == 1
    assert body["summary"]["profile_count"] == 1
    assert len(body["quarantined"]) == 1
    assert body["summary"]["quarantined_count"] == 1
    assert body["projected"][0]["primary_email"]["value"] == "p.sharma@workmail.com"


def test_bad_config_json_is_422() -> None:
    """Malformed config JSON is the caller's fault -> 422, never 500."""
    res = client.post(
        "/transform",
        files=[_upload(ATS, "application/json")],
        data={"config": "{not valid json"},
    )
    assert res.status_code == 422
    assert "Invalid config" in res.json()["detail"]


def test_bad_config_schema_is_422() -> None:
    """Valid JSON that violates the Config schema is also a 422."""
    res = client.post(
        "/transform",
        files=[_upload(ATS, "application/json")],
        data={"config": json.dumps({"fields": "not-a-list"})},
    )
    assert res.status_code == 422


def test_garbage_source_is_200_and_quarantined() -> None:
    """A garbage source never errors the request; it comes back quarantined."""
    res = client.post("/transform", files=[_upload(BROKEN, "application/json")])
    assert res.status_code == 200
    body = res.json()
    assert body["profiles"] == []
    assert len(body["quarantined"]) == 1
    assert body["quarantined"][0]["path"].endswith("broken_source.json")


def test_default_config_round_trips() -> None:
    """Absent config yields the full canonical projection (full_name present)."""
    res = client.post(
        "/transform",
        files=[_upload(ATS, "application/json"), _upload(RESUME, "text/plain")],
    )
    assert res.status_code == 200
    projected = res.json()["projected"][0]
    assert projected["full_name"]["value"] == "Priya Sharma"


def _normalize_paths(payload: dict[str, Any]) -> dict[str, Any]:
    """Reduce quarantine paths to basenames so temp-file vs real-file paths compare."""
    for record in payload["quarantined"]:
        record["path"] = Path(record["path"]).name
    return payload


def test_cli_output_matches_api() -> None:
    """The CLI emits the identical TransformResponse as the API for the same inputs."""
    api_res = client.post(
        "/transform",
        files=_all_samples(),
        data={"config": CONFIG.read_text(encoding="utf-8")},
    )
    assert api_res.status_code == 200

    cli_response = transform_inputs([str(ATS), str(RESUME), str(BROKEN)], str(CONFIG))
    cli_payload: dict[str, Any] = json.loads(cli_response.model_dump_json())

    assert _normalize_paths(cli_payload) == _normalize_paths(api_res.json())
