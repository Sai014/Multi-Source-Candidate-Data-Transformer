"""ATS JSON adapter.

The ATS blob uses its own (foreign) field names, and different ATS vendors disagree
on those names. Rather than bind to one vendor, each canonical field is resolved
from a list of case-insensitive aliases (:data:`_FIELD_ALIASES`), and contact/address
sub-objects are flattened so nested shapes (``ContactInfo.Email``) resolve too. Every
value becomes a single ``DIRECT_MAP`` claim from ``SourceType.ATS``; structured entries
are built as typed models - never loose dicts. Missing/null keys produce no claim;
malformed JSON raises, so the detection framework quarantines the source.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
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
from app.normalize import normalize
from app.sources.base import register_adapter

# Canonical field -> accepted foreign aliases (compared case-insensitively).
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "full_name": ("candidateName", "applicantName", "name", "fullName", "candidate_name"),
    "emails": ("emailAddresses", "emails", "email", "emailAddress", "email_addresses"),
    "phones": ("mobile", "phone", "phones", "phoneNumber", "phone_number", "cell", "telephone"),
    "skills": ("skills", "systemTags", "tags", "skillSet", "skill_set"),
}
# Foreign keys whose value is a list of structured entries.
_EXPERIENCE_KEYS = ("workHistory", "employmentHistory", "experience", "employment", "work_history")
_EDUCATION_KEYS = ("schools", "academicBackground", "education", "academics", "academic_background")
# Sub-objects flattened into the scalar lookup (so ContactInfo.Email resolves).
_CONTACT_CONTAINER_KEYS = ("contactInfo", "contact", "contactDetails")
_SOCIAL_KEYS = ("socials", "social", "profileUrl", "profileUrls", "website", "links")
_LOCATION_KEYS = ("location", "currentAddress", "address", "currentLocation", "geo")

# Per-entry aliases for structured experience/education rows.
_EXP_COMPANY = ("employer", "employerName", "company", "organization", "companyName")
_EXP_TITLE = ("role", "positionTitle", "jobTitle", "title", "position")
_EXP_START = ("startDate", "start", "from", "beginDate")
_EXP_END = ("endDate", "end", "to", "finishDate")
_EXP_SUMMARY = ("notes", "roleDescription", "summary", "description", "responsibilities")
_EDU_INSTITUTION = ("school", "institution", "university", "college", "schoolName")
_EDU_DEGREE = ("credential", "degree", "qualification")
_EDU_FIELD = ("studyArea", "major", "field", "fieldOfStudy", "specialization")
_EDU_YEAR = ("graduationYear", "gradYear", "year", "end_year", "completionYear", "graduation_year")
_LOC_CITY = ("city", "town", "locality")
_LOC_REGION = ("region", "state", "province")
_LOC_COUNTRY = ("country", "nation")


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
    """Return string items of a list, or a single string wrapped, else empty."""
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []


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


def _ci_index(data: dict[str, object]) -> dict[str, object]:
    """Build a lowercased-key view of a mapping for case-insensitive lookup."""
    return {key.lower(): value for key, value in data.items()}


def _lookup(index: dict[str, object], aliases: Sequence[str]) -> object | None:
    """Return the first present alias value from a lowercased-key index."""
    for alias in aliases:
        if alias.lower() in index:
            return index[alias.lower()]
    return None


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


def _scalar_index(data: dict[str, object]) -> dict[str, object]:
    """Top-level keys plus the contents of any contact sub-object, lowercased."""
    index = _ci_index(data)
    scalar = dict(index)
    for container_key in _CONTACT_CONTAINER_KEYS:
        container = _as_mapping(index.get(container_key.lower()))
        if container is not None:
            for key, value in _ci_index(container).items():
                scalar.setdefault(key, value)
    return scalar


def _classify_socials(urls: Sequence[str]) -> Links | None:
    """Classify raw social URLs into a typed :class:`Links` via the url normalizer."""
    linkedin: str | None = None
    github: str | None = None
    other: list[str] = []
    for url in urls:
        result = normalize("url_link", url)
        if not result.ok or not isinstance(result.value, str):
            continue
        if result.note == "linkedin":
            linkedin = linkedin or result.value
        elif result.note == "github":
            github = github or result.value
        else:
            other.append(result.value)
    if linkedin is None and github is None and not other:
        return None
    return Links(linkedin=linkedin, github=github, other=sorted(set(other)))


def _build_links(value: object) -> Links | None:
    """Build links from an explicit links object with classified sub-fields."""
    data = _as_mapping(value)
    if data is None:
        return None
    index = _ci_index(data)
    linkedin = _as_str(_lookup(index, ("linkedin",)))
    github = _as_str(_lookup(index, ("github",)))
    portfolio = _as_str(_lookup(index, ("portfolio", "website", "personal")))
    other = _as_str_list(index.get("other"))
    if linkedin is None and github is None and portfolio is None and not other:
        return None
    return Links(linkedin=linkedin, github=github, portfolio=portfolio, other=other)


def _build_location(value: object) -> Location | None:
    data = _as_mapping(value)
    if data is None:
        return None
    index = _ci_index(data)
    city = _as_str(_lookup(index, _LOC_CITY))
    region = _as_str(_lookup(index, _LOC_REGION))
    country = _as_str(_lookup(index, _LOC_COUNTRY))
    if city is None and region is None and country is None:
        return None
    return Location(city=city, region=region, country=country)


def _build_experience(value: object) -> ExperienceEntry | None:
    data = _as_mapping(value)
    if data is None:
        return None
    index = _ci_index(data)
    company = _as_str(_lookup(index, _EXP_COMPANY))
    title = _as_str(_lookup(index, _EXP_TITLE))
    start = _as_str(_lookup(index, _EXP_START))
    end = _as_str(_lookup(index, _EXP_END))
    summary = _as_str(_lookup(index, _EXP_SUMMARY))
    if company is None and title is None and start is None and end is None and summary is None:
        return None
    return ExperienceEntry(company=company, title=title, start=start, end=end, summary=summary)


def _build_education(value: object) -> EducationEntry | None:
    data = _as_mapping(value)
    if data is None:
        return None
    index = _ci_index(data)
    institution = _as_str(_lookup(index, _EDU_INSTITUTION))
    degree = _as_str(_lookup(index, _EDU_DEGREE))
    field = _as_str(_lookup(index, _EDU_FIELD))
    end_year = _as_int(_lookup(index, _EDU_YEAR))
    if institution is None and degree is None and field is None and end_year is None:
        return None
    return EducationEntry(institution=institution, degree=degree, field=field, end_year=end_year)


class AtsAdapter:
    """Adapter for the structured ATS JSON source (vendor-agnostic field names)."""

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

        index = _ci_index(data)
        scalar = _scalar_index(data)
        claims: list[Claim] = []

        full_name = _as_str(_lookup(scalar, _FIELD_ALIASES["full_name"]))
        if full_name is not None:
            claims.append(_ats_claim("full_name", full_name, full_name))

        for email in _as_str_list(_lookup(scalar, _FIELD_ALIASES["emails"])):
            claims.append(_ats_claim("emails", email, email))
        for phone in _as_str_list(_lookup(scalar, _FIELD_ALIASES["phones"])):
            claims.append(_ats_claim("phones", phone, phone))
        for skill in _as_str_list(_lookup(index, _FIELD_ALIASES["skills"])):
            claims.append(_ats_claim("skills", skill, skill))

        claims.extend(self._link_claims(scalar))

        location_raw = _lookup(index, _LOCATION_KEYS)
        location = _build_location(location_raw)
        if location is not None:
            claims.append(_ats_claim("location", location, _raw_json(location_raw)))

        claims.extend(self._current_experience(scalar))
        for entry in _as_list(_lookup(index, _EXPERIENCE_KEYS)):
            experience = _build_experience(entry)
            if experience is not None:
                claims.append(_ats_claim("experience", experience, _raw_json(entry)))

        for entry in _as_list(_lookup(index, _EDUCATION_KEYS)):
            education = _build_education(entry)
            if education is not None:
                claims.append(_ats_claim("education", education, _raw_json(entry)))

        return claims

    @staticmethod
    def _link_claims(scalar: dict[str, object]) -> list[Claim]:
        """Build link claims from an explicit links object and/or social URL fields."""
        claims: list[Claim] = []
        explicit = _build_links(scalar.get("links"))
        if explicit is not None:
            claims.append(_ats_claim("links", explicit, _raw_json(scalar.get("links"))))

        social_urls: list[str] = []
        for key in _SOCIAL_KEYS:
            if key == "links":
                continue
            social_urls.extend(_as_str_list(scalar.get(key.lower())))
        social = _classify_socials(social_urls)
        if social is not None:
            claims.append(_ats_claim("links", social, _raw_json(social_urls)))
        return claims

    @staticmethod
    def _current_experience(scalar: dict[str, object]) -> list[Claim]:
        """Build an experience entry from currentEmployer/jobTitle, when present."""
        company = _as_str(_lookup(scalar, ("currentEmployer", "current_employer")))
        title = _as_str(_lookup(scalar, ("jobTitle", "job_title", "currentTitle")))
        if company is None and title is None:
            return []
        entry = ExperienceEntry(company=company, title=title)
        raw = _raw_json({"currentEmployer": company, "jobTitle": title})
        return [_ats_claim("experience", entry, raw)]


register_adapter(AtsAdapter())
