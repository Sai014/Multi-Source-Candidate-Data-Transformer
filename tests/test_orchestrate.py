"""End-to-end orchestration tests (Step 9 DoD)."""

from __future__ import annotations

from pathlib import Path

from app.domain.models import Config
from app.pipeline.orchestrate import RunResult, run

SAMPLES = Path(__file__).resolve().parents[1] / "samples"

INPUTS = [
    SAMPLES / "ats_sample.json",
    SAMPLES / "resume_sample.txt",
    SAMPLES / "broken_source.json",
]


def test_end_to_end_shape() -> None:
    """One profile, one quarantine record, and a clean default projection."""
    result = run(INPUTS, Config())

    assert isinstance(result, RunResult)
    assert len(result.profiles) == 1
    assert len(result.projections) == 1
    assert len(result.reports) == 1
    assert len(result.quarantined) == 1

    assert result.reports[0].ok
    assert result.profiles[0].full_name == "Priya Sharma"


def test_broken_source_is_quarantined_with_reason() -> None:
    """The malformed JSON is quarantined with a structured reason, not crashed."""
    result = run(INPUTS, Config())
    quarantined = result.quarantined[0]
    assert quarantined.path.endswith("broken_source.json")
    assert quarantined.reason


def test_projection_view_matches_profile() -> None:
    """The default projection round-trips the fused canonical profile."""
    result = run(INPUTS, Config())
    profile = result.profiles[0]
    values = result.projections[0].values
    dumped = profile.model_dump(mode="json")
    assert values["full_name"] == dumped["full_name"]
    assert values["emails"] == dumped["emails"]


def test_no_inputs_yields_empty_result() -> None:
    """An empty input list produces no profiles and no quarantine records."""
    result = run([], Config())
    assert result.profiles == []
    assert result.quarantined == []


def test_unroutable_path_is_quarantined() -> None:
    """A path no adapter recognizes is quarantined with no_adapter, not crashed."""
    result = run([SAMPLES / "nope.unknownext"], Config())
    assert result.profiles == []
    assert len(result.quarantined) == 1
    assert result.quarantined[0].reason == "no_adapter"


def test_custom_config_projection_runs_end_to_end() -> None:
    """A custom config projects end-to-end and reports the missing required field."""
    import json

    raw = json.loads((SAMPLES / "config_custom.json").read_text(encoding="utf-8"))
    config = Config.model_validate(raw)
    result = run(INPUTS, config)

    assert len(result.profiles) == 1
    values = result.projections[0].values
    assert values["primary_email"] == "p.sharma@workmail.com"
    reasons = {(v.path, v.reason) for v in result.reports[0].violations}
    assert ("twitter", "missing_required") in reasons
