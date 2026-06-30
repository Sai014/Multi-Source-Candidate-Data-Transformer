"""Regression tests for the grader's Alex Rivera inputs.

These cover the four issues that the foreign-keyed ATS blob exposed: vendor-agnostic
ATS field resolution, source merging, experience extraction, and clean
education/skill normalization, plus the bare-value projection schema.
"""

from __future__ import annotations

from pathlib import Path

from app.domain.models import (
    Claim,
    ClaimValue,
    Config,
    EducationEntry,
    ExperienceEntry,
    Links,
)
from app.pipeline.orchestrate import run
from app.sources.ats import AtsAdapter

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
ATS = SAMPLES / "ats_alex.json"
RESUME = SAMPLES / "resume_alex.txt"


def _values(field: str, claims: list[Claim]) -> list[ClaimValue]:
    return [c.value for c in claims if c.field == field]


def test_ats_alias_format_is_extracted() -> None:
    """The foreign-keyed ATS blob (ContactInfo/EmploymentHistory/...) resolves."""
    claims = AtsAdapter().extract(ATS)

    assert _values("full_name", claims) == ["Alex Rivera"]
    assert _values("emails", claims) == ["arivera.dev@email.com"]
    assert _values("phones", claims) == ["415.555.0199"]
    assert set(_values("skills", claims)) == {"Python", "Django", "SQL", "REST APIs", "Git"}

    experiences = _values("experience", claims)
    assert len(experiences) == 2
    companies = {e.company for e in experiences if isinstance(e, ExperienceEntry)}
    assert companies == {"CloudSync Technologies", "DataFlow Inc"}

    education = _values("education", claims)
    assert len(education) == 1
    entry = education[0]
    assert isinstance(entry, EducationEntry)
    assert entry.institution == "San Jose State University"
    assert entry.degree == "B.S."
    assert entry.field == "Software Engineering"
    assert entry.end_year == 2021

    links = _values("links", claims)
    assert any(isinstance(link, Links) and link.linkedin for link in links)


def test_sources_merge_into_one_profile() -> None:
    """ATS + resume for the same person collapse into a single canonical profile."""
    result = run([ATS, RESUME], Config())
    assert len(result.profiles) == 1
    assert result.quarantined == []

    profile = result.profiles[0]
    assert profile.full_name == "Alex Rivera"
    assert "arivera.dev@email.com" in profile.emails
    assert "+14155550199" in profile.phones


def test_experience_is_populated_and_normalized() -> None:
    """The experience block (missing before) is present with normalized dates."""
    profile = run([ATS, RESUME], Config()).profiles[0]
    companies = {e.company for e in profile.experience}
    assert {"CloudSync Technologies", "DataFlow Inc"} <= companies

    cloudsync = next(e for e in profile.experience if e.company == "CloudSync Technologies")
    assert cloudsync.start == "2023-03"
    assert cloudsync.end is None  # "Current" -> present


def test_education_and_skills_are_clean() -> None:
    """Education is split into parts and skills carry no category-label prefixes."""
    profile = run([ATS, RESUME], Config()).profiles[0]

    edu = profile.education[0]
    assert edu.institution == "San Jose State University"
    assert edu.degree == "B.S."
    assert edu.field == "Software Engineering"

    names = [s.name for s in profile.skills]
    assert all(":" not in name for name in names)
    assert not any(name.startswith("Languages") or name.startswith("Tools") for name in names)


def test_default_projection_is_bare_canonical_schema() -> None:
    """With no config the projection is the plain canonical schema (bare values)."""
    result = run([ATS, RESUME], Config())
    values = result.projections[0].values
    assert values["full_name"] == "Alex Rivera"
    assert isinstance(values["emails"], list)
    assert isinstance(values["provenance"], list)
    assert result.projections[0].meta == {}
