"""Gold-profile determinism test against the real sample inputs.

Runs the real pipeline (extract -> cluster -> fuse) on the checked-in samples and
asserts the produced profile matches a checked-in expected JSON exactly. This is
the end-to-end determinism proof for the pure core.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.pipeline.fuse import fuse
from app.pipeline.resolve import cluster
from app.sources.ats import AtsAdapter
from app.sources.resume import ResumeAdapter

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def _build_profile() -> dict[str, Any]:
    records = [
        AtsAdapter().extract(SAMPLES / "ats_sample.json"),
        ResumeAdapter().extract(SAMPLES / "resume_sample.txt"),
    ]
    clusters = cluster(records)
    assert len(clusters) == 1
    profile = fuse(clusters[0])
    dumped: dict[str, Any] = profile.model_dump(mode="json")
    return dumped


def test_gold_profile_matches_expected() -> None:
    """The fused profile from the samples equals the checked-in expected JSON."""
    expected = json.loads((SAMPLES / "expected_profile.json").read_text(encoding="utf-8"))
    assert _build_profile() == expected


def test_pipeline_is_deterministic_across_runs() -> None:
    """Re-running the whole pipeline yields byte-identical output."""
    assert _build_profile() == _build_profile()
