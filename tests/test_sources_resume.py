"""Tests for the multi-method resume adapter."""

from __future__ import annotations

from pathlib import Path

from app.domain.enums import ExtractionMethod, SourceType
from app.domain.models import Claim, EducationEntry, ExperienceEntry
from app.sources.resume import ProseExtractor, ResumeAdapter, SectionExtractor

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
_RESUME_TEXT = (SAMPLES / "resume_sample.txt").read_text(encoding="utf-8")


def _fields(claims: list[Claim], field: str) -> list[Claim]:
    return [claim for claim in claims if claim.field == field]


def test_both_methods_fire_on_resume() -> None:
    """Running the adapter exercises both the structured and prose strategies."""
    claims = ResumeAdapter().extract(SAMPLES / "resume_sample.txt")
    methods = {claim.method for claim in claims}
    assert ExtractionMethod.STRUCTURED_PARSE in methods
    assert ExtractionMethod.REGEX_PROSE in methods
    assert all(claim.source is SourceType.RESUME for claim in claims)


def test_same_email_appears_as_two_claims_with_different_method() -> None:
    """The shared email is claimed once per strategy, distinguished by method."""
    claims = ResumeAdapter().extract(SAMPLES / "resume_sample.txt")
    email_claims = [c for c in _fields(claims, "emails") if c.value == "priya.sharma@example.com"]
    methods = {c.method for c in email_claims}
    assert len(email_claims) >= 2
    assert methods == {ExtractionMethod.STRUCTURED_PARSE, ExtractionMethod.REGEX_PROSE}


def test_section_extractor_yields_structured_claims() -> None:
    """The section extractor pulls name, skills, experience, and education."""
    claims = SectionExtractor().extract(_RESUME_TEXT)
    assert all(c.method is ExtractionMethod.STRUCTURED_PARSE for c in claims)

    names = _fields(claims, "full_name")
    assert [c.value for c in names] == ["Priya Sharma"]

    skills = {c.value for c in _fields(claims, "skills")}
    assert {"Python", "Apache Spark", "AWS"} <= skills

    experiences = [c.value for c in _fields(claims, "experience")]
    assert any(
        isinstance(e, ExperienceEntry) and e.company == "Northwind Analytics" for e in experiences
    )

    education = [c.value for c in _fields(claims, "education")]
    assert any(
        isinstance(e, EducationEntry)
        and e.institution == "University of California, Berkeley"
        for e in education
    )


def test_prose_extractor_yields_contact_and_years() -> None:
    """The prose extractor independently finds email, phone, and years phrases."""
    claims = ProseExtractor().extract(_RESUME_TEXT)
    assert all(c.method is ExtractionMethod.REGEX_PROSE for c in claims)
    assert any(c.value == "priya.sharma@example.com" for c in _fields(claims, "emails"))
    assert len(_fields(claims, "phones")) >= 1

    years = _fields(claims, "years_experience")
    assert len(years) == 1
    assert isinstance(years[0].value, str)
    assert "7" in years[0].value


def test_extractors_never_raise_on_odd_formatting() -> None:
    """Odd or empty input degrades to fewer claims rather than raising."""
    for odd in ("", "@@@\n###\n!!!", "no structure at all"):
        assert isinstance(SectionExtractor().extract(odd), list)
        assert isinstance(ProseExtractor().extract(odd), list)


def test_adapter_degrades_on_garbage_file(tmp_path: Path) -> None:
    """The adapter returns a (possibly small) claim list for a garbage text file."""
    garbage = tmp_path / "weird.txt"
    garbage.write_text("\x00\x01 ??? \n no fields here", encoding="utf-8")
    claims = ResumeAdapter().extract(garbage)
    assert isinstance(claims, list)
