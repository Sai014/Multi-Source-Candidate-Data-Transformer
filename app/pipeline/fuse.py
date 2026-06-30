"""Fusion / adjudication: turn a cluster of claims into one canonical profile.

This is pure core - no I/O, no clock, no randomness. The same cluster always
produces the same :class:`CanonicalProfile`. Every value is traceable to the
claims that produced it (provenance), and the system stays honestly-empty: a
winner below the honesty gate is withheld as null rather than asserted.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.enums import ExtractionMethod, SourceType
from app.domain.models import (
    CanonicalProfile,
    Claim,
    ClaimValue,
    EducationEntry,
    ExperienceEntry,
    Links,
    Location,
    Provenance,
    Skill,
)
from app.normalize import normalize
from app.pipeline.confidence import (
    DISAGREEMENT_PENALTY,
    HIGH_TRUST_THRESHOLD,
    TAU,
    claim_confidence,
    noisy_or,
    source_priority,
)

_FIELD_NORMALIZER: dict[str, str] = {
    "full_name": "name",
    "emails": "email",
    "phones": "phone_e164",
    "skills": "skill_canonical",
    "headline": "text_unicode",
    "years_experience": "years_experience",
}
_CONFIDENCE_PRECISION = 4
_ProvKey = tuple[str, SourceType, ExtractionMethod, str | None]


def _round(value: float) -> float:
    return round(value, _CONFIDENCE_PRECISION)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


# --------------------------------------------------------------------------- #
# Per-claim normalization                                                     #
# --------------------------------------------------------------------------- #


def _norm_text(value: str | None) -> str | None:
    if value is None:
        return None
    result = normalize("text_unicode", value)
    return result.value if result.ok and isinstance(result.value, str) else None


def _norm_url(value: str | None) -> str | None:
    if value is None:
        return None
    result = normalize("url_link", value)
    return result.value if result.ok and isinstance(result.value, str) else None


def _norm_date(value: str | None) -> str | None:
    if value is None:
        return None
    result = normalize("date_ym", value)
    return result.value if result.ok and isinstance(result.value, str) else None


def _normalize_location(location: Location) -> Location:
    country: str | None = None
    if location.country is not None:
        result = normalize("country_iso2", location.country)
        country = result.value if result.ok and isinstance(result.value, str) else None
    return Location(
        city=_norm_text(location.city),
        region=_norm_text(location.region),
        country=country,
    )


def _normalize_links(links: Links) -> Links:
    other = sorted({u for url in links.other if (u := _norm_url(url)) is not None})
    return Links(
        linkedin=_norm_url(links.linkedin),
        github=_norm_url(links.github),
        portfolio=_norm_url(links.portfolio),
        other=other,
    )


def _normalize_experience(entry: ExperienceEntry) -> ExperienceEntry:
    return ExperienceEntry(
        company=_norm_text(entry.company),
        title=_norm_text(entry.title),
        start=_norm_date(entry.start),
        end=_norm_date(entry.end),
        summary=_norm_text(entry.summary),
    )


def _normalize_education(entry: EducationEntry) -> EducationEntry:
    return EducationEntry(
        institution=_norm_text(entry.institution),
        degree=_norm_text(entry.degree),
        field=_norm_text(entry.field),
        end_year=entry.end_year,
    )


def _normalize_claim(claim: Claim) -> Claim:
    """Return a copy of ``claim`` with normalized value, ok flag, and confidence."""
    confidence = claim_confidence(claim.source, claim.method)
    value = claim.value
    field = claim.field

    if field in _FIELD_NORMALIZER and isinstance(value, str):
        result = normalize(_FIELD_NORMALIZER[field], value)
        return claim.model_copy(
            update={"normalized": result.value, "normalize_ok": result.ok, "confidence": confidence}
        )
    if field == "location" and isinstance(value, Location):
        location = _normalize_location(value)
        ok = any((location.city, location.region, location.country))
        return claim.model_copy(
            update={"normalized": location, "normalize_ok": ok, "confidence": confidence}
        )
    if field == "links" and isinstance(value, Links):
        links = _normalize_links(value)
        ok = any((links.linkedin, links.github, links.portfolio, links.other))
        return claim.model_copy(
            update={"normalized": links, "normalize_ok": ok, "confidence": confidence}
        )
    if field == "experience" and isinstance(value, ExperienceEntry):
        experience = _normalize_experience(value)
        ok = any((experience.company, experience.title, experience.start, experience.end))
        return claim.model_copy(
            update={"normalized": experience, "normalize_ok": ok, "confidence": confidence}
        )
    if field == "education" and isinstance(value, EducationEntry):
        education = _normalize_education(value)
        ok = any((education.institution, education.degree, education.field, education.end_year))
        return claim.model_copy(
            update={"normalized": education, "normalize_ok": ok, "confidence": confidence}
        )
    return claim.model_copy(
        update={"normalized": value, "normalize_ok": True, "confidence": confidence}
    )


# --------------------------------------------------------------------------- #
# Scalar adjudication                                                         #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _ScalarOutcome:
    value: ClaimValue | None
    confidence: float
    contributors: tuple[Claim, ...]
    losers: tuple[Claim, ...]
    withheld: bool


def _confidences(claims: Sequence[Claim]) -> list[float]:
    return [claim.confidence for claim in claims if claim.confidence is not None]


def _fuse_scalar(pairs: Sequence[tuple[ClaimValue | None, Claim]]) -> _ScalarOutcome:
    """Pick a winning scalar value, combining agreement and gating low confidence."""
    candidates = [(value, claim) for value, claim in pairs if value is not None]
    if not candidates:
        return _ScalarOutcome(None, 0.0, (), (), False)

    by_value: dict[ClaimValue, list[Claim]] = {}
    for value, claim in candidates:
        by_value.setdefault(value, []).append(claim)

    scored = [
        (value, claims, noisy_or(_confidences(claims)),
         min(source_priority(c.source, c.method) for c in claims))
        for value, claims in by_value.items()
    ]
    scored.sort(key=lambda item: (-item[2], item[3], str(item[0])))
    winner_value, winner_claims, winner_confidence, _ = scored[0]
    losers = [claim for value, claims, _, _ in scored[1:] for claim in claims]

    if any((claim.confidence or 0.0) >= HIGH_TRUST_THRESHOLD for claim in losers):
        winner_confidence = max(0.0, winner_confidence - DISAGREEMENT_PENALTY)

    if winner_confidence < TAU:
        all_claims = tuple(claim for _, claim in candidates)
        return _ScalarOutcome(None, winner_confidence, (), all_claims, True)
    return _ScalarOutcome(
        winner_value, winner_confidence, tuple(winner_claims), tuple(losers), False
    )


# --------------------------------------------------------------------------- #
# List adjudication                                                           #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _ListOutcome:
    values: list[str]
    confidence: float
    contributors: tuple[Claim, ...]


def _group_normalized_strings(claims: Sequence[Claim]) -> dict[str, list[Claim]]:
    grouped: dict[str, list[Claim]] = {}
    for claim in claims:
        if claim.normalize_ok and isinstance(claim.normalized, str):
            grouped.setdefault(claim.normalized, []).append(claim)
    return grouped


def _fuse_string_list(claims: Sequence[Claim]) -> _ListOutcome:
    grouped = _group_normalized_strings(claims)
    values = sorted(grouped)
    confidences = [noisy_or(_confidences(grouped[value])) for value in values]
    contributors = tuple(claim for value in values for claim in grouped[value])
    return _ListOutcome(values, _mean(confidences), contributors)


def _fuse_skills(claims: Sequence[Claim]) -> tuple[list[Skill], float, tuple[Claim, ...]]:
    grouped = _group_normalized_strings(claims)
    skills: list[Skill] = []
    contributors: list[Claim] = []
    confidences: list[float] = []
    for name in sorted(grouped):
        members = grouped[name]
        confidence = _round(noisy_or(_confidences(members)))
        sources = sorted({claim.source.value for claim in members})
        skills.append(Skill(name=name, confidence=confidence, sources=sources))
        confidences.append(confidence)
        contributors.extend(members)
    return skills, _mean(confidences), tuple(contributors)


# --------------------------------------------------------------------------- #
# Structured-list and composite adjudication                                  #
# --------------------------------------------------------------------------- #


def _fuse_location(claims: Sequence[Claim]) -> tuple[Location, float, tuple[Claim, ...]]:
    items = [
        (claim.normalized, claim)
        for claim in claims
        if claim.normalize_ok and isinstance(claim.normalized, Location)
    ]
    city = _fuse_scalar([(loc.city, claim) for loc, claim in items])
    region = _fuse_scalar([(loc.region, claim) for loc, claim in items])
    country = _fuse_scalar([(loc.country, claim) for loc, claim in items])
    location = Location(
        city=city.value if isinstance(city.value, str) else None,
        region=region.value if isinstance(region.value, str) else None,
        country=country.value if isinstance(country.value, str) else None,
    )
    confidences = [o.confidence for o in (city, region, country) if o.value is not None]
    return location, _mean(confidences), tuple(claim for _, claim in items)


def _fuse_links(claims: Sequence[Claim]) -> tuple[Links, float, tuple[Claim, ...]]:
    items = [
        (claim.normalized, claim)
        for claim in claims
        if claim.normalize_ok and isinstance(claim.normalized, Links)
    ]
    linkedin = _fuse_scalar([(link.linkedin, claim) for link, claim in items])
    github = _fuse_scalar([(link.github, claim) for link, claim in items])
    portfolio = _fuse_scalar([(link.portfolio, claim) for link, claim in items])
    other = sorted({url for link, _ in items for url in link.other})
    links = Links(
        linkedin=linkedin.value if isinstance(linkedin.value, str) else None,
        github=github.value if isinstance(github.value, str) else None,
        portfolio=portfolio.value if isinstance(portfolio.value, str) else None,
        other=other,
    )
    confidences = [o.confidence for o in (linkedin, github, portfolio) if o.value is not None]
    return links, _mean(confidences), tuple(claim for _, claim in items)


def _prefer_direct_map(claims: Sequence[Claim]) -> tuple[list[Claim], list[Claim]]:
    """Split structured-list claims into preferred (ATS direct-map) and superseded.

    When any ``DIRECT_MAP`` claim is present it is authoritative for the field, so
    lower-trust parsed entries (e.g. a noisy resume section) are superseded rather
    than unioned in - this keeps experience/education clean. With no direct-map
    claim, every claim is kept.
    """
    direct = [c for c in claims if c.method == ExtractionMethod.DIRECT_MAP]
    if direct:
        superseded = [c for c in claims if c.method is not ExtractionMethod.DIRECT_MAP]
        return direct, superseded
    return list(claims), []


def _fuse_experience(
    claims: Sequence[Claim],
) -> tuple[list[ExperienceEntry], tuple[Claim, ...], tuple[Claim, ...]]:
    preferred, superseded = _prefer_direct_map(claims)
    merged: dict[tuple[str, str], ExperienceEntry] = {}
    contributors: list[Claim] = []
    for claim in preferred:
        entry = claim.normalized
        if not (claim.normalize_ok and isinstance(entry, ExperienceEntry)):
            continue
        contributors.append(claim)
        key = ((entry.company or "").lower(), (entry.title or "").lower())
        if key not in merged:
            merged[key] = entry
            continue
        existing = merged[key]
        merged[key] = existing.model_copy(
            update={
                "company": existing.company or entry.company,
                "title": existing.title or entry.title,
                "start": existing.start or entry.start,
                "end": existing.end or entry.end,
                "summary": existing.summary or entry.summary,
            }
        )
    entries = sorted(
        merged.values(),
        key=lambda e: ((e.start or ""), (e.company or ""), (e.title or "")),
        reverse=True,
    )
    return entries, tuple(contributors), tuple(superseded)


def _fuse_education(
    claims: Sequence[Claim],
) -> tuple[list[EducationEntry], tuple[Claim, ...], tuple[Claim, ...]]:
    preferred, superseded = _prefer_direct_map(claims)
    merged: dict[tuple[str, str, int], EducationEntry] = {}
    contributors: list[Claim] = []
    for claim in preferred:
        entry = claim.normalized
        if not (claim.normalize_ok and isinstance(entry, EducationEntry)):
            continue
        contributors.append(claim)
        key = ((entry.institution or "").lower(), (entry.degree or "").lower(), entry.end_year or 0)
        if key not in merged:
            merged[key] = entry
            continue
        existing = merged[key]
        merged[key] = existing.model_copy(
            update={
                "institution": existing.institution or entry.institution,
                "degree": existing.degree or entry.degree,
                "field": existing.field or entry.field,
            }
        )
    entries = sorted(
        merged.values(),
        key=lambda e: ((e.end_year or 0), (e.institution or "")),
        reverse=True,
    )
    return entries, tuple(contributors), tuple(superseded)


# --------------------------------------------------------------------------- #
# Identity and provenance                                                     #
# --------------------------------------------------------------------------- #


def _candidate_id(emails: Sequence[str], links: Links, full_name: str | None,
                  phones: Sequence[str]) -> str:
    """sha1 of the strongest available key: email > profile URL > composite."""
    if emails:
        key = emails[0]
    else:
        urls = [u for u in (links.linkedin, links.github, links.portfolio) if u] + list(links.other)
        if urls:
            key = sorted(urls)[0]
        else:
            parts = [part for part in [full_name, *phones] if part]
            key = "|".join(sorted(parts))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def _scalar_pairs(claims: Sequence[Claim]) -> list[tuple[ClaimValue | None, Claim]]:
    pairs: list[tuple[ClaimValue | None, Claim]] = []
    for claim in claims:
        normalized = claim.normalized
        if not claim.normalize_ok or isinstance(normalized, bool):
            continue
        if isinstance(normalized, str | int | float):
            pairs.append((normalized, claim))
    return pairs


def _as_float(value: ClaimValue | None) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


# --------------------------------------------------------------------------- #
# Orchestration                                                               #
# --------------------------------------------------------------------------- #


def fuse(cluster: Sequence[Claim]) -> CanonicalProfile:
    """Adjudicate one candidate's claims into a single canonical profile."""
    prepared = [_normalize_claim(claim) for claim in cluster]
    by_field: dict[str, list[Claim]] = {}
    for claim in prepared:
        by_field.setdefault(claim.field, []).append(claim)

    provenance: set[_ProvKey] = set()

    def add_provenance(claims: Sequence[Claim], note: str | None) -> None:
        for claim in claims:
            provenance.add((claim.field, claim.source, claim.method, note))

    def record_scalar(outcome: _ScalarOutcome) -> None:
        if outcome.withheld:
            add_provenance(outcome.losers, "withheld_low_confidence")
        else:
            add_provenance(outcome.contributors, None)
            add_provenance(outcome.losers, "superseded")

    core_confidences: list[float] = []

    name_out = _fuse_scalar(_scalar_pairs(by_field.get("full_name", [])))
    full_name = name_out.value if isinstance(name_out.value, str) else None
    record_scalar(name_out)
    if full_name is not None:
        core_confidences.append(name_out.confidence)

    headline_out = _fuse_scalar(_scalar_pairs(by_field.get("headline", [])))
    headline = headline_out.value if isinstance(headline_out.value, str) else None
    record_scalar(headline_out)
    if headline is not None:
        core_confidences.append(headline_out.confidence)

    years_out = _fuse_scalar(_scalar_pairs(by_field.get("years_experience", [])))
    years_experience = _as_float(years_out.value)
    record_scalar(years_out)
    if years_experience is not None:
        core_confidences.append(years_out.confidence)

    emails_out = _fuse_string_list(by_field.get("emails", []))
    if emails_out.values:
        add_provenance(emails_out.contributors, None)
        core_confidences.append(emails_out.confidence)

    phones_out = _fuse_string_list(by_field.get("phones", []))
    if phones_out.values:
        add_provenance(phones_out.contributors, None)
        core_confidences.append(phones_out.confidence)

    skills, skills_confidence, skills_contrib = _fuse_skills(by_field.get("skills", []))
    if skills:
        add_provenance(skills_contrib, None)
        core_confidences.append(skills_confidence)

    location, location_confidence, location_contrib = _fuse_location(by_field.get("location", []))
    add_provenance(location_contrib, None)
    if any((location.city, location.region, location.country)):
        core_confidences.append(location_confidence)

    links, links_confidence, links_contrib = _fuse_links(by_field.get("links", []))
    add_provenance(links_contrib, None)
    if any((links.linkedin, links.github, links.portfolio, links.other)):
        core_confidences.append(links_confidence)

    experience, experience_contrib, experience_super = _fuse_experience(
        by_field.get("experience", [])
    )
    add_provenance(experience_contrib, None)
    add_provenance(experience_super, "superseded")
    education, education_contrib, education_super = _fuse_education(
        by_field.get("education", [])
    )
    add_provenance(education_contrib, None)
    add_provenance(education_super, "superseded")

    candidate_id = _candidate_id(emails_out.values, links, full_name, phones_out.values)
    overall_confidence = _round(_mean(core_confidences))

    return CanonicalProfile(
        candidate_id=candidate_id,
        full_name=full_name,
        emails=emails_out.values,
        phones=phones_out.values,
        location=location,
        links=links,
        headline=headline,
        years_experience=years_experience,
        skills=skills,
        experience=experience,
        education=education,
        provenance=_build_provenance(provenance),
        overall_confidence=overall_confidence,
    )


def _build_provenance(entries: set[_ProvKey]) -> list[Provenance]:
    ordered = sorted(
        entries, key=lambda item: (item[0], item[1].value, item[2].value, item[3] or "")
    )
    return [
        Provenance(field=field, source=source, method=method, note=note)
        for field, source, method, note in ordered
    ]
