"""Tests for config-driven projection and validation (Step 8 DoD)."""

from __future__ import annotations

import json
from pathlib import Path

from app.domain.enums import ExtractionMethod, OnMissing, SourceType
from app.domain.models import (
    CanonicalProfile,
    Config,
    FieldSpec,
    Links,
    Location,
    Provenance,
    Skill,
)
from app.pipeline.project import ProjectedValue, project
from app.pipeline.validate import validate_view

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def _profile() -> CanonicalProfile:
    return CanonicalProfile(
        candidate_id="abc123",
        full_name="Priya Sharma",
        emails=["p.sharma@workmail.com", "priya.sharma@example.com"],
        phones=["+14155550182"],
        location=Location(city="San Francisco", region="California", country="US"),
        links=Links(
            linkedin="https://www.linkedin.com/in/priya-sharma",
            github="https://github.com/priyasharma",
        ),
        skills=[
            Skill(name="Python", confidence=0.96, sources=["ats", "resume"]),
            Skill(name="SQL", confidence=0.96, sources=["ats", "resume"]),
        ],
        provenance=[
            Provenance(
                field="emails",
                source=SourceType.ATS,
                method=ExtractionMethod.DIRECT_MAP,
            )
        ],
        overall_confidence=0.9262,
    )


# --------------------------------------------------------------------------- #
# Default projection round-trips                                              #
# --------------------------------------------------------------------------- #


def test_default_projection_round_trips() -> None:
    """An empty config yields the full canonical profile, value-for-value."""
    profile = _profile()
    view, report = project(profile, Config())

    assert report.ok
    dumped = profile.model_dump(mode="json")
    assert set(view) == set(dumped)
    for key, value in dumped.items():
        assert view[key].value == value
    assert view["full_name"].confidence is None
    assert view["full_name"].provenance is None


# --------------------------------------------------------------------------- #
# Custom config (the Step 8 DoD)                                              #
# --------------------------------------------------------------------------- #


def _custom_config() -> Config:
    raw = json.loads((SAMPLES / "config_custom.json").read_text(encoding="utf-8"))
    return Config.model_validate(raw)


def test_custom_config_remaps_array_index() -> None:
    """emails[0] projects to primary_email."""
    view, _ = project(_profile(), _custom_config())
    assert view["primary_email"].value == "p.sharma@workmail.com"


def test_custom_config_applies_normalize() -> None:
    """A normalize token re-runs the registry function on the resolved value."""
    view, _ = project(_profile(), _custom_config())
    assert view["phone"].value == "+14155550182"


def test_custom_config_maps_over_list_and_normalizes() -> None:
    """skills[].name maps to a list of names, canonicalized."""
    view, _ = project(_profile(), _custom_config())
    assert view["skill_names"].value == ["Python", "SQL"]


def test_custom_config_toggles_confidence_on() -> None:
    """include_confidence attaches a confidence to each projected value."""
    view, _ = project(_profile(), _custom_config())
    assert view["primary_email"].confidence == 0.9262


def test_missing_required_field_produces_violation() -> None:
    """A required field with no resolvable value is a violation regardless of policy."""
    _, report = project(_profile(), _custom_config())
    assert not report.ok
    reasons = {(v.path, v.reason) for v in report.violations}
    assert ("twitter", "missing_required") in reasons


# --------------------------------------------------------------------------- #
# Toggles / on_missing policies                                               #
# --------------------------------------------------------------------------- #


def test_confidence_toggle_off_leaves_none() -> None:
    """Without include_confidence, projected values carry no confidence."""
    config = Config(fields=[FieldSpec(path="full_name", type="string")])
    view, _ = project(_profile(), config)
    assert view["full_name"].confidence is None


def test_include_provenance_filters_by_root_field() -> None:
    """include_provenance attaches the canonical field's provenance entries."""
    config = Config(
        fields=[FieldSpec(path="primary_email", from_="emails[0]", type="string")],
        include_provenance=True,
    )
    view, _ = project(_profile(), config)
    provenance = view["primary_email"].provenance
    assert provenance is not None
    assert [p.field for p in provenance] == ["emails"]


def test_on_missing_omit_drops_field() -> None:
    """on_missing=omit removes an optional missing field from the view entirely."""
    config = Config(
        fields=[FieldSpec(path="headline", type="string")],
        on_missing=OnMissing.OMIT,
    )
    view, report = project(_profile(), config)
    assert "headline" not in view
    assert report.ok


def test_on_missing_null_keeps_field_as_null() -> None:
    """on_missing=null keeps an optional missing field with a null value."""
    config = Config(
        fields=[FieldSpec(path="headline", type="string")],
        on_missing=OnMissing.NULL,
    )
    view, report = project(_profile(), config)
    assert "headline" in view
    assert view["headline"].value is None
    assert report.ok


def test_on_missing_error_records_violation_and_omits() -> None:
    """on_missing=error records a violation and emits no value for the field."""
    config = Config(
        fields=[FieldSpec(path="headline", type="string")],
        on_missing=OnMissing.ERROR,
    )
    view, report = project(_profile(), config)
    assert "headline" not in view
    assert not report.ok
    assert report.violations[0].reason == "missing"


# --------------------------------------------------------------------------- #
# Validation                                                                  #
# --------------------------------------------------------------------------- #


def test_validate_flags_type_mismatch() -> None:
    """A value that violates its declared type yields a type_mismatch violation."""
    config = Config(fields=[FieldSpec(path="emails", type="string")])
    view, _ = project(_profile(), config)
    report = validate_view(view, config)
    assert not report.ok
    assert report.violations[0].reason == "type_mismatch"


def test_validate_accepts_conforming_view() -> None:
    """A conforming custom projection passes type validation for present fields."""
    config = _custom_config()
    view, _ = project(_profile(), config)
    report = validate_view(view, config)
    type_violations = [v for v in report.violations if v.reason == "type_mismatch"]
    assert type_violations == []


def test_validate_string_list_type() -> None:
    """A string[] field accepts a list of strings."""
    config = Config(fields=[FieldSpec(path="emails", type="string[]")])
    view, _ = project(_profile(), config)
    report = validate_view(view, config)
    assert report.ok


def test_projection_is_deterministic() -> None:
    """Projecting the same profile/config twice yields identical views."""
    config = _custom_config()
    profile = _profile()
    first, _ = project(profile, config)
    second, _ = project(profile, config)
    assert {k: v.model_dump() for k, v in first.items()} == {
        k: v.model_dump() for k, v in second.items()
    }


def test_projected_value_constructs_nested() -> None:
    """ProjectedValue accepts nested resolved structures (a dict value)."""
    pv = ProjectedValue(value={"city": "SF", "country": "US"})
    assert pv.value == {"city": "SF", "country": "US"}
