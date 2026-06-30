"""Tests for the multi-method resume adapter."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from app.domain.enums import ExtractionMethod, SourceType
from app.domain.models import Claim, EducationEntry, ExperienceEntry, Location
from app.sources.resume import (
    GlinerExtractor,
    ProseExtractor,
    ResumeAdapter,
    SectionExtractor,
    _dedupe_entities,
    _filter_redundant_ner_claims,
    _split_gliner_chunks,
)

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
_RESUME_TEXT = (SAMPLES / "resume_sample.txt").read_text(encoding="utf-8")

_SAMPLE_ENTITIES = [
    {"text": "Priya Sharma", "label": "person", "start": 0, "end": 12, "score": 0.95},
    {"text": "San Francisco, CA", "label": "location", "start": 13, "end": 30, "score": 0.90},
    {
        "text": "Senior Data Engineer",
        "label": "job title",
        "start": 200,
        "end": 220,
        "score": 0.88,
    },
    {
        "text": "Northwind Analytics",
        "label": "organization",
        "start": 221,
        "end": 240,
        "score": 0.91,
    },
    {
        "text": "University of California, Berkeley",
        "label": "university",
        "start": 500,
        "end": 534,
        "score": 0.92,
    },
    {
        "text": "B.S. in Computer Science",
        "label": "degree",
        "start": 480,
        "end": 504,
        "score": 0.89,
    },
]

_SAMIRA_TEXT = """\
Samira Jones
London, UK | sam.jones@gmail.com | +44 20 7946 0958
EXPERIENCE
Data Scientist - TechCorp
Built predictive models for customer churn. (4 years)
Junior Analyst - FinBank
August 2018 - December 2019
Data cleaning and dashboard creation using Tableau.
EDUCATION
PhD in Computer Science, University of Oxford (2018)
"""

_SANDEEP_TEXT = """\
Sai Sandeep R
sandeep.5112004@gmail.com | +91 95131 89613
Education
Sir M Visvesvaraya Institute of Technology Bengaluru
Bachelor of Engineering in Computer Science (CGPA: 9.05) 2022 – 2026
Sindhi High School Bengaluru
Secondary Education (XII) – 93.6% 2020 – 2022
Experience
Appscrip Bengaluru
Python AI Intern Feb 2026 – Present
Numa Soft Technology Services Pvt. Ltd. Bengaluru
Backend Developer Intern Apr 2024 – Jun 2024
Skills
Python, Tem-
poral, FastAPI
"""


@pytest.fixture(autouse=True)
def _mock_gliner(request: pytest.FixtureRequest) -> Iterator[None]:
    """Keep resume tests fast and deterministic without loading the GLiNER model."""
    if request.node.get_closest_marker("no_gliner_mock") is not None:
        yield
        return
    with patch("app.sources.resume._predict_entities", return_value=[]):
        yield


def _fields(claims: list[Claim], field: str) -> list[Claim]:
    return [claim for claim in claims if claim.field == field]


def test_all_methods_fire_on_resume(_mock_gliner: None) -> None:
    """Running the adapter exercises structured, prose, and NER strategies."""
    with patch("app.sources.resume._predict_entities", return_value=_SAMPLE_ENTITIES):
        claims = ResumeAdapter().extract(SAMPLES / "resume_sample.txt")
    methods = {claim.method for claim in claims}
    assert ExtractionMethod.STRUCTURED_PARSE in methods
    assert ExtractionMethod.REGEX_PROSE in methods
    assert ExtractionMethod.NER in methods
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
        assert isinstance(GlinerExtractor().extract(odd), list)


def test_gliner_extractor_yields_ner_claims(_mock_gliner: None) -> None:
    """The GLiNER extractor emits NER claims with mapped canonical fields."""
    with patch("app.sources.resume._predict_entities", return_value=_SAMPLE_ENTITIES):
        claims = GlinerExtractor().extract(_RESUME_TEXT)
    assert all(c.method is ExtractionMethod.NER for c in claims)
    assert all(c.source is SourceType.RESUME for c in claims)

    names = _fields(claims, "full_name")
    assert any(c.value == "Priya Sharma" for c in names)

    locations = _fields(claims, "location")
    assert any(
        isinstance(loc.value, Location) and loc.value.city == "San Francisco"
        for loc in locations
    )

    experiences = [c.value for c in _fields(claims, "experience")]
    assert any(
        isinstance(entry, ExperienceEntry)
        and entry.title == "Senior Data Engineer"
        and entry.company == "Northwind Analytics"
        for entry in experiences
    )

    education = [c.value for c in _fields(claims, "education")]
    assert any(
        isinstance(entry, EducationEntry)
        and entry.institution == "University of California, Berkeley"
        and entry.degree == "B.S."
        for entry in education
    )


def test_adapter_degrades_on_garbage_file(tmp_path: Path) -> None:
    """The adapter returns a (possibly small) claim list for a garbage text file."""
    garbage = tmp_path / "weird.txt"
    garbage.write_text("\x00\x01 ??? \n no fields here", encoding="utf-8")
    claims = ResumeAdapter().extract(garbage)
    assert isinstance(claims, list)


def test_split_gliner_chunks_keeps_short_text_intact() -> None:
    """Short resumes are sent to GLiNER as a single chunk."""
    text = "Priya Sharma\nSan Francisco, CA"
    assert _split_gliner_chunks(text) == [(text, 0)]


def test_split_gliner_chunks_splits_long_text_with_overlap() -> None:
    """Long resumes are split into overlapping windows under the char budget."""
    line = "Senior Data Engineer at Northwind Analytics building data platforms.\n"
    text = line * 80
    chunks = _split_gliner_chunks(text)
    assert len(chunks) > 1
    assert chunks[0][1] == 0
    assert all(chunk for chunk, _ in chunks)
    for index in range(1, len(chunks)):
        prev_end = chunks[index - 1][1] + len(chunks[index - 1][0])
        assert chunks[index][1] < prev_end


def test_dedupe_entities_keeps_highest_score() -> None:
    """Overlapping chunk windows merge duplicate label/text pairs deterministically."""
    entities = _dedupe_entities([
        {"text": "Priya Sharma", "label": "person", "start": 0, "end": 12, "score": 0.80},
        {"text": "Priya Sharma", "label": "person", "start": 50, "end": 62, "score": 0.95},
    ])
    assert len(entities) == 1
    assert entities[0]["score"] == 0.95


@pytest.mark.no_gliner_mock
def test_predict_entities_runs_one_call_per_chunk() -> None:
    """Chunked prediction invokes GLiNER once per window."""
    line = "Experienced engineer with Python, Spark, and AWS experience.\n"
    text = line * 100
    chunks = _split_gliner_chunks(text)
    calls: list[str] = []

    class _FakeModel:
        def predict_entities(
            self,
            chunk: str,
            labels: list[str],
            threshold: float = 0.5,
        ) -> list[dict[str, str | int | float]]:
            calls.append(chunk)
            return []

    with patch("app.sources.resume._load_gliner_model", return_value=_FakeModel()):
        from app.sources.resume import _predict_entities

        assert _predict_entities(text) == []
    assert len(calls) == len(chunks)


def test_section_extractor_parses_dash_experience_and_parens_education() -> None:
    """Common PDF layouts with ``Title - Company`` and parenthetical education parse cleanly."""
    claims = SectionExtractor().extract(_SAMIRA_TEXT)
    experiences = [c.value for c in _fields(claims, "experience") if isinstance(c.value, ExperienceEntry)]
    assert any(e.title == "Data Scientist" and e.company == "TechCorp" for e in experiences)
    assert any(e.title == "Junior Analyst" and e.company == "FinBank" for e in experiences)
    assert any(e.start == "August 2018" and e.end == "December 2019" for e in experiences)

    education = [c.value for c in _fields(claims, "education") if isinstance(c.value, EducationEntry)]
    assert len(education) == 1
    assert education[0].degree == "PhD"
    assert education[0].field == "Computer Science"
    assert education[0].institution == "University of Oxford"
    assert education[0].end_year == 2018


def test_section_extractor_pairs_institution_first_education_and_company_blocks() -> None:
    """Institution-first education and company/title blocks produce complete entries."""
    claims = SectionExtractor().extract(_SANDEEP_TEXT)
    education = [c.value for c in _fields(claims, "education") if isinstance(c.value, EducationEntry)]
    assert any(
        e.institution == "Sir M Visvesvaraya Institute of Technology Bengaluru"
        and e.degree == "Bachelor of Engineering"
        and e.field == "Computer Science"
        and e.end_year == 2026
        for e in education
    )
    assert any(
        e.institution == "Sindhi High School Bengaluru"
        and e.degree == "Secondary Education (XII)"
        and e.end_year == 2022
        for e in education
    )

    experiences = [c.value for c in _fields(claims, "experience") if isinstance(c.value, ExperienceEntry)]
    assert any(
        e.company == "Appscrip Bengaluru"
        and e.title == "Python AI Intern"
        and e.end == "Present"
        for e in experiences
    )
    assert any(
        e.company == "Numa Soft Technology Services Pvt. Ltd. Bengaluru"
        and e.title == "Backend Developer Intern"
        for e in experiences
    )

    skills = {c.value for c in _fields(claims, "skills")}
    assert "Temporal" in skills
    assert "Tem-" not in skills


def test_filter_redundant_ner_claims_drops_structured_duplicates() -> None:
    """NER fragments and duplicates are removed when structured claims already cover them."""
    structured = SectionExtractor().extract(_SANDEEP_TEXT)
    noisy_ner = [
        Claim(
            field="experience",
            value=ExperienceEntry(company="gy Services Pvt. Ltd.", title="Backend Developer Intern"),
            source=SourceType.RESUME,
            method=ExtractionMethod.NER,
            raw="gy Services Pvt. Ltd.",
        ),
        Claim(
            field="experience",
            value=ExperienceEntry(company="Google Cloud"),
            source=SourceType.RESUME,
            method=ExtractionMethod.NER,
            raw="Google Cloud",
        ),
        Claim(
            field="education",
            value=EducationEntry(degree="Bachelor of Engineering", field="Computer Science"),
            source=SourceType.RESUME,
            method=ExtractionMethod.NER,
            raw="Bachelor of Engineering in Computer Science",
        ),
    ]
    filtered = _filter_redundant_ner_claims([*structured, *noisy_ner])
    ner_experience = [
        c for c in filtered if c.field == "experience" and c.method is ExtractionMethod.NER
    ]
    ner_education = [
        c for c in filtered if c.field == "education" and c.method is ExtractionMethod.NER
    ]
    assert ner_experience == []
    assert ner_education == []
