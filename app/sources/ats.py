"""ATS JSON adapter.

The ATS blob uses its own (foreign) field names. :data:`ATS_FIELD_MAP` makes the
translation to canonical fields explicit. Every value becomes a single
``DIRECT_MAP`` claim from ``SourceType.ATS``. Structured entries are built as typed
models - never loose dicts. Missing or null keys produce no claim; malformed JSON
raises, so the detection framework quarantines the source.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.domain.enums import ExtractionMethod, SourceType
from app.domain.models import (
    Claim,
    ClaimValue,
    EducationEntry,
    ExperienceEntry,
    Links,
    Location,
)
from app.sources.base import register_adapter

ATS_FIELD_MAP: dict[str, str] = {
    "candidateName": "full_name",
    "emailAddresses": "emails",
    "mobile": "phones",
    "skills": "skills",
    "location": "location",
    "links": "links",
    "currentEmployer": "experience",
    "jobTitle": "experience",
    "workHistory": "experience",
    "schools": "education",
}

_SCALAR_KEYS = ("candidateName", "mobile")
_LIST_KEYS = ("emailAddresses", "skills")


def _as_str(value: object) -> str | None:
    """Return a non-empty string, or None for anything else/blank."""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _as_int(value: object) -> int | None:
    """Return an int from an int or digit-string; None otherwise (bools excluded)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _as_str_list(value: object) -> list[str]:
    """Return the string items of a list; empty for non-lists."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _as_list(value: object) -> list[object]:
    """Return the items of a list as objects; empty for non-lists."""
    if not isinstance(value, list):
        return []
    return list(value)


def _as_mapping(value: object) -> dict[str, object] | None:
    """Return a string-keyed mapping view of a dict, or None for non-dicts."""
    if not isinstance(value, dict):
        return None
    return {str(key): item for key, item in value.items()}


def _raw_json(value: object) -> str:
    """Serialize a value deterministically for the provenance ``raw`` field."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _ats_claim(field: str, value: ClaimValue, raw: str) -> Claim:
    return Claim(
        field=field,
        value=value,
        source=SourceType.ATS,
        method=ExtractionMethod.DIRECT_MAP,
        raw=raw,
    )


def _build_location(value: object) -> Location | None:
    data = _as_mapping(value)
    if data is None:
        return None
    city = _as_str(data.get("city"))
    region = _as_str(data.get("region"))
    country = _as_str(data.get("country"))
    if city is None and region is None and country is None:
        return None
    return Location(city=city, region=region, country=country)


def _build_links(value: object) -> Links | None:
    data = _as_mapping(value)
    if data is None:
        return None
    linkedin = _as_str(data.get("linkedin"))
    github = _as_str(data.get("github"))
    portfolio = _as_str(data.get("portfolio"))
    other = _as_str_list(data.get("other"))
    if linkedin is None and github is None and portfolio is None and not other:
        return None
    return Links(linkedin=linkedin, github=github, portfolio=portfolio, other=other)


def _build_experience(value: object) -> ExperienceEntry | None:
    data = _as_mapping(value)
    if data is None:
        return None
    company = _as_str(data.get("employer"))
    title = _as_str(data.get("role"))
    start = _as_str(data.get("startDate"))
    end = _as_str(data.get("endDate"))
    summary = _as_str(data.get("notes"))
    if company is None and title is None and start is None and end is None and summary is None:
        return None
    return ExperienceEntry(company=company, title=title, start=start, end=end, summary=summary)


def _build_current_experience(data: dict[str, object]) -> ExperienceEntry | None:
    company = _as_str(data.get("currentEmployer"))
    title = _as_str(data.get("jobTitle"))
    if company is None and title is None:
        return None
    return ExperienceEntry(company=company, title=title)


def _build_education(value: object) -> EducationEntry | None:
    data = _as_mapping(value)
    if data is None:
        return None
    institution = _as_str(data.get("school"))
    degree = _as_str(data.get("credential"))
    field = _as_str(data.get("studyArea"))
    end_year = _as_int(data.get("graduationYear"))
    if institution is None and degree is None and field is None and end_year is None:
        return None
    return EducationEntry(institution=institution, degree=degree, field=field, end_year=end_year)


class AtsAdapter:
    """Adapter for the structured ATS JSON source."""

    source_type = SourceType.ATS

    def can_handle(self, path: Path) -> bool:
        """Handle any ``.json`` input (malformed content is quarantined on extract)."""
        return path.suffix.lower() == ".json"

    def extract(self, path: Path) -> list[Claim]:
        """Parse the ATS JSON at ``path`` into typed claims."""
        parsed = json.loads(path.read_text(encoding="utf-8"))
        data = _as_mapping(parsed)
        if data is None:
            raise ValueError("ATS source root must be a JSON object")

        claims: list[Claim] = []

        for key in _SCALAR_KEYS:
            scalar = _as_str(data.get(key))
            if scalar is not None:
                claims.append(_ats_claim(ATS_FIELD_MAP[key], scalar, scalar))

        for key in _LIST_KEYS:
            for item in _as_str_list(data.get(key)):
                claims.append(_ats_claim(ATS_FIELD_MAP[key], item, item))

        location = _build_location(data.get("location"))
        if location is not None:
            claims.append(_ats_claim("location", location, _raw_json(data.get("location"))))

        links = _build_links(data.get("links"))
        if links is not None:
            claims.append(_ats_claim("links", links, _raw_json(data.get("links"))))

        current = _build_current_experience(data)
        if current is not None:
            raw = _raw_json(
                {"currentEmployer": data.get("currentEmployer"), "jobTitle": data.get("jobTitle")}
            )
            claims.append(_ats_claim("experience", current, raw))

        for entry in _as_list(data.get("workHistory")):
            experience = _build_experience(entry)
            if experience is not None:
                claims.append(_ats_claim("experience", experience, _raw_json(entry)))

        for entry in _as_list(data.get("schools")):
            education = _build_education(entry)
            if education is not None:
                claims.append(_ats_claim("education", education, _raw_json(entry)))

        return claims


register_adapter(AtsAdapter())
