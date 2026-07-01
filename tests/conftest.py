"""Shared pytest hooks and fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _deterministic_gliner(request: pytest.FixtureRequest) -> Iterator[None]:
    """Keep tests fast and deterministic unless they opt into real GLiNER."""
    skip_predict = request.node.get_closest_marker("no_gliner_mock") is not None
    preload_patch = patch("app.sources.resume.gliner.preload_gliner_model")
    predict_patch = (
        patch("app.sources.resume.extractors._predict_entities", return_value=[])
        if not skip_predict
        else None
    )
    with preload_patch:
        if predict_patch is not None:
            with predict_patch:
                yield
        else:
            yield
