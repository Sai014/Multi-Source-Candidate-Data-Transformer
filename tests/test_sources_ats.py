"""Tests for the ATS JSON adapter."""

from __future__ import annotations

from pathlib import Path

from app.domain.enums import ExtractionMethod, SourceType
from app.domain.models import Claim, EducationEntry, ExperienceEntry, Links, Location
from app.sources.ats import AtsAdapter
from app.sources.detect import ingest_paths

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def _by_field(claims: list[Claim], field: str) -> list[Claim]:
    return [claim for claim in claims if claim.field == field]


def test_ats_sample_yields_expected_typed_claims() -> None:
    """The ATS sample maps to the expected canonical claims with typed values."""
    claims = AtsAdapter().extract(SAMPLES / "ats_sample.json")

    assert all(c.source is SourceType.ATS for c in claims)
    assert all(c.method is ExtractionMethod.DIRECT_MAP for c in claims)

    names = _by_field(claims, "full_name")
    assert [c.value for c in names] == ["Priya Sharma"]

    emails = {c.value for c in _by_field(claims, "emails")}
    assert emails == {"priya.sharma@example.com", "p.sharma@workmail.com"}

    phones = [c.value for c in _by_field(claims, "phones")]
    assert phones == ["+1 (415) 555-0182"]

    skills = {c.value for c in _by_field(claims, "skills")}
    assert {"Python", "Apache Spark", "AWS", "SQL", "Airflow"} == skills


def test_ats_location_and_links_are_typed_models() -> None:
    """Nested location/links become typed models, not loose dicts."""
    claims = AtsAdapter().extract(SAMPLES / "ats_sample.json")

    location = _by_field(claims, "location")[0].value
    assert isinstance(location, Location)
    assert location.city == "San Francisco"
    assert location.region == "California"
    assert location.country == "United States"

    links = _by_field(claims, "links")[0].value
    assert isinstance(links, Links)
    assert links.linkedin is not None and "linkedin.com/in/priya-sharma" in links.linkedin
    assert links.github is not None and "github.com/priyasharma" in links.github


def test_ats_experience_and_education_entries() -> None:
    """currentEmployer/jobTitle and workHistory both yield ExperienceEntry claims."""
    claims = AtsAdapter().extract(SAMPLES / "ats_sample.json")

    experiences = [c.value for c in _by_field(claims, "experience")]
    assert len(experiences) == 3
    assert all(isinstance(e, ExperienceEntry) for e in experiences)
    companies = {e.company for e in experiences if isinstance(e, ExperienceEntry)}
    assert {"Northwind Analytics", "BluePeak Software"} <= companies

    bluepeak = next(
        e
        for e in experiences
        if isinstance(e, ExperienceEntry) and e.company == "BluePeak Software"
    )
    assert bluepeak.start == "2018-07"
    assert bluepeak.end == "2021-02"

    education = _by_field(claims, "education")
    assert len(education) == 1
    entry = education[0].value
    assert isinstance(entry, EducationEntry)
    assert entry.institution == "University of California, Berkeley"
    assert entry.degree == "B.S."
    assert entry.field == "Computer Science"
    assert entry.end_year == 2018


def test_broken_source_is_quarantined_not_crashed() -> None:
    """Malformed JSON is quarantined by the framework, never crashing the run."""
    result = ingest_paths([SAMPLES / "broken_source.json"], adapters=[AtsAdapter()])
    assert len(result.ledger) == 0
    assert len(result.quarantined) == 1
    assert result.quarantined[0].source is SourceType.ATS
    assert "broken_source.json" in result.quarantined[0].path


def test_good_and_broken_sources_ingested_together() -> None:
    """A good source still produces claims even when a sibling source is broken."""
    result = ingest_paths(
        [SAMPLES / "ats_sample.json", SAMPLES / "broken_source.json"],
        adapters=[AtsAdapter()],
    )
    assert len(result.ledger) == 15
    assert len(result.quarantined) == 1
