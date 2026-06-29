"""Smoke tests ensuring the package and its subpackages import cleanly."""

from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize(
    "module_name",
    [
        "app",
        "app.domain",
        "app.normalize",
        "app.sources",
        "app.pipeline",
        "app.api",
        "app.api.main",
        "app.cli",
    ],
)
def test_module_imports(module_name: str) -> None:
    """Every scaffolded module imports without error."""
    assert importlib.import_module(module_name) is not None


def test_cli_main_runs(capsys: pytest.CaptureFixture[str]) -> None:
    """The CLI runs end-to-end on a sample and prints JSON with the response shape."""
    import json
    from pathlib import Path

    from app.cli import main

    ats = Path(__file__).resolve().parents[1] / "samples" / "ats_sample.json"
    assert main(["--inputs", str(ats), "--config", "none"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert "profiles" in payload
    assert "summary" in payload
