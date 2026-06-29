"""Construction and behavior tests for the domain models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.enums import ExtractionMethod, OnMissing, SourceType
from app.domain.models import (
    CanonicalProfile,
    Claim,
    Config,
    EducationEntry,
    ExperienceEntry,
    FieldSpec,
    Links,
    Location,
    Provenance,
    Skill,
)


def test_enum_values_are_stable_strings() -> None:
    """Enums serialize to the fixed lowercase string values."""
    assert SourceType.ATS == "ats"
    assert ExtractionMethod.DIRECT_MAP == "direct_map"
    assert OnMissing.NULL == "null"


def test_construct_one_of_each_model() -> None:
    """Every domain model constructs with precise types."""
    location = Location(city="San Francisco", region="California", country="US")
    links = Links(linkedin="https://linkedin.com/in/x", other=["https://x.dev"])
    experience = ExperienceEntry(
        company="Northwind", title="Engineer", start="2021-03", end=None, summary="Built things."
    )
    education = EducationEntry(
        institution="UC Berkeley", degree="B.S.", field="CS", end_year=2018
    )
    skill = Skill(name="python", confidence=0.9, sources=["ats", "resume"])

    claim = Claim(
        field="full_name",
        value="Priya Sharma",
        source=SourceType.ATS,
        method=ExtractionMethod.DIRECT_MAP,
        raw="Priya Sharma",
    )
    provenance = Provenance(
        field="full_name", source=SourceType.ATS, method=ExtractionMethod.DIRECT_MAP, note=None
    )

    profile = CanonicalProfile(
        candidate_id="A-100294",
        full_name="Priya Sharma",
        emails=["priya@example.com"],
        phones=["+14155550182"],
        location=location,
        links=links,
        skills=[skill],
        experience=[experience],
        education=[education],
        provenance=[provenance],
        overall_confidence=0.87,
    )

    assert profile.full_name == "Priya Sharma"
    assert profile.skills[0].name == "python"
    assert claim.value == "Priya Sharma"


def test_claim_is_frozen() -> None:
    """A Claim is immutable after creation."""
    claim = Claim(
        field="headline",
        value="Senior Data Engineer",
        source=SourceType.RESUME,
        method=ExtractionMethod.REGEX_PROSE,
        raw="Senior Data Engineer",
    )
    with pytest.raises(ValidationError):
        claim.value = "changed"  # type: ignore[misc]


def test_claim_accepts_structured_value() -> None:
    """ClaimValue admits structured models, not just primitives."""
    entry = ExperienceEntry(company="BluePeak", title="Data Engineer")
    claim = Claim(
        field="experience",
        value=entry,
        source=SourceType.ATS,
        method=ExtractionMethod.STRUCTURED_PARSE,
        raw=None,
    )
    assert isinstance(claim.value, ExperienceEntry)
    assert claim.value.company == "BluePeak"


def test_fieldspec_from_alias_roundtrip() -> None:
    """FieldSpec reads/writes the reserved ``from`` key via aliasing."""
    spec = FieldSpec.model_validate(
        {"path": "phone", "from": "phones[0]", "type": "string", "normalize": "E164"}
    )
    assert spec.from_ == "phones[0]"
    assert spec.model_dump(by_alias=True)["from"] == "phones[0]"


def test_config_defaults() -> None:
    """Config defaults match the spec (on_missing=null, flags off)."""
    config = Config(fields=[FieldSpec(path="full_name", type="string", required=True)])
    assert config.on_missing is OnMissing.NULL
    assert config.include_confidence is False
    assert config.include_provenance is False
