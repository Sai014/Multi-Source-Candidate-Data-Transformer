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


def test_cli_main_runs() -> None:
    """The CLI stub exits cleanly with code 0."""
    from app.cli import main

    assert main([]) == 0
