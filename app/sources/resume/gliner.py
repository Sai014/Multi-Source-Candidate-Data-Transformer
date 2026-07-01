"""GLiNER NER extraction for resume documents."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from functools import lru_cache
from typing import Protocol, TypedDict, cast

from app.domain.enums import ExtractionMethod
from app.domain.models import Claim, EducationEntry, ExperienceEntry
from app.sources.resume.text import (
    _education_year,
    _location_from_text,
    _looks_like_degree,
    _resume_claim,
    _split_degree_field,
)

logger = logging.getLogger(__name__)

_GLINER_MODEL_ID = "urchade/gliner_medium-v2.1"
_GLINER_THRESHOLD = 0.5
# GLiNER truncates at 384 tokens; ~3.5 chars/token is conservative for resume prose.
_GLINER_MAX_CHARS = 1200
_GLINER_OVERLAP_CHARS = 200
_GLINER_LABELS: tuple[str, ...] = (
    "person",
    "organization",
    "university",
    "degree",
    "job title",
    "certification",
    "location",
)


class _GlinerEntity(TypedDict):
    text: str
    label: str
    start: int
    end: int
    score: float


class _GlinerModel(Protocol):
    def predict_entities(
        self,
        text: str,
        labels: list[str],
        threshold: float = ...,
    ) -> list[_GlinerEntity]: ...


@lru_cache(maxsize=1)
def _load_gliner_model() -> _GlinerModel:
    from gliner import GLiNER

    return cast(_GlinerModel, GLiNER.from_pretrained(_GLINER_MODEL_ID))


def preload_gliner_model() -> None:
    """Download and warm the GLiNER model cache (idempotent)."""
    logger.info("Loading GLiNER model %s", _GLINER_MODEL_ID)
    _load_gliner_model()


def _split_gliner_chunks(text: str) -> list[tuple[str, int]]:
    """Split resume text into overlapping windows that fit GLiNER's token limit."""
    if len(text) <= _GLINER_MAX_CHARS:
        return [(text, 0)]

    chunks: list[tuple[str, int]] = []
    start = 0
    while start < len(text):
        end = min(start + _GLINER_MAX_CHARS, len(text))
        if end < len(text):
            newline = text.rfind("\n", start, end)
            if newline > start:
                end = newline + 1
        chunks.append((text[start:end], start))
        if end >= len(text):
            break
        start = max(start + 1, end - _GLINER_OVERLAP_CHARS)
    return chunks


def _entity_key(entity: _GlinerEntity) -> tuple[str, str]:
    return (entity["label"].strip().lower(), entity["text"].strip())


def _globalize_entity(entity: _GlinerEntity, chunk_start: int) -> _GlinerEntity:
    return {
        "text": entity["text"],
        "label": entity["label"],
        "start": entity["start"] + chunk_start,
        "end": entity["end"] + chunk_start,
        "score": entity["score"],
    }


def _merge_entities(existing: _GlinerEntity, incoming: _GlinerEntity) -> _GlinerEntity:
    """Keep the higher-scoring span; ties favor the earlier global offset."""
    if incoming["score"] > existing["score"]:
        return incoming
    if incoming["score"] < existing["score"]:
        return existing
    return incoming if incoming["start"] < existing["start"] else existing


def _dedupe_entities(entities: Iterable[_GlinerEntity]) -> list[_GlinerEntity]:
    merged: dict[tuple[str, str], _GlinerEntity] = {}
    for entity in entities:
        key = _entity_key(entity)
        cleaned = entity["text"].strip()
        if not cleaned:
            continue
        if key not in merged:
            merged[key] = entity
        else:
            merged[key] = _merge_entities(merged[key], entity)
    return sorted(
        merged.values(),
        key=lambda entity: (entity["start"], entity["end"], entity["label"], entity["text"]),
    )


def _predict_entities_on_chunk(model: _GlinerModel, chunk: str) -> list[_GlinerEntity]:
    return model.predict_entities(
        chunk,
        list(_GLINER_LABELS),
        threshold=_GLINER_THRESHOLD,
    )


def _predict_entities(text: str) -> list[_GlinerEntity]:
    """Run GLiNER over chunked resume text; return deduped entities in stable order."""
    model = _load_gliner_model()
    all_entities: list[_GlinerEntity] = []
    for chunk, chunk_start in _split_gliner_chunks(text):
        chunk_entities = _predict_entities_on_chunk(model, chunk)
        all_entities.extend(_globalize_entity(entity, chunk_start) for entity in chunk_entities)
    return _dedupe_entities(all_entities)


_NER_PROXIMITY = 250
_NON_EMPLOYER_ORGANIZATIONS = frozenset({
    "google cloud",
    "aws",
    "azure",
    "docker",
    "kubernetes",
    "postgresql",
    "mongodb",
    "redis",
    "fastapi",
    "python",
})


def _entity_distance(left: _GlinerEntity, right: _GlinerEntity) -> int:
    if left["end"] <= right["start"]:
        return right["start"] - left["end"]
    if right["end"] <= left["start"]:
        return left["start"] - right["end"]
    return 0


def _closest_entity(
    anchor: _GlinerEntity,
    candidates: Sequence[_GlinerEntity],
    used: set[int],
) -> tuple[_GlinerEntity | None, int | None]:
    best: _GlinerEntity | None = None
    best_index: int | None = None
    best_distance = _NER_PROXIMITY + 1
    for index, candidate in enumerate(candidates):
        if index in used:
            continue
        distance = _entity_distance(anchor, candidate)
        if distance < best_distance:
            best_distance = distance
            best = candidate
            best_index = index
    if best is None:
        return None, None
    return best, best_index


def _education_from_degree_text(text: str) -> tuple[str, str | None]:
    if _looks_like_degree(text):
        return _split_degree_field(text)
    return text.strip(), None


def _is_org_fragment(short: str, organizations: Sequence[str]) -> bool:
    short_lower = short.lower()
    for other in organizations:
        other_lower = other.lower()
        if other_lower == short_lower:
            continue
        if short_lower in other_lower and len(other_lower) - len(short_lower) > 4:
            return True
    return False


def _usable_organizations(entities: Sequence[_GlinerEntity]) -> list[_GlinerEntity]:
    names = [entity["text"].strip() for entity in entities]
    usable: list[_GlinerEntity] = []
    for entity in entities:
        name = entity["text"].strip()
        if not name:
            continue
        if name.lower() in _NON_EMPLOYER_ORGANIZATIONS:
            continue
        if _is_org_fragment(name, names):
            continue
        usable.append(entity)
    return usable


def _is_low_quality_ner_experience(entry: ExperienceEntry) -> bool:
    company = (entry.company or "").strip()
    title = (entry.title or "").strip()
    if company and company.lower() in _NON_EMPLOYER_ORGANIZATIONS:
        return True
    return bool(company and not title)


def _is_ner_experience_redundant(
    ner: ExperienceEntry,
    structured: ExperienceEntry,
) -> bool:
    ner_title = (ner.title or "").lower()
    structured_title = (structured.title or "").lower()
    ner_company = (ner.company or "").lower()
    structured_company = (structured.company or "").lower()

    if ner_title and structured_title and ner_title != structured_title:
        return False
    if not ner_title and structured_title:
        return False

    if ner_company and structured_company:
        if ner_company == structured_company:
            return True
        if ner_company in structured_company and len(structured_company) - len(ner_company) > 4:
            return True
    return bool(not ner_company and structured_company and ner_title == structured_title)


def _is_ner_education_redundant(
    ner: EducationEntry,
    structured: EducationEntry,
) -> bool:
    ner_degree = (ner.degree or "").lower()
    structured_degree = (structured.degree or "").lower()
    ner_field = (ner.field or "").lower()
    structured_field = (structured.field or "").lower()
    ner_institution = (ner.institution or "").lower()
    structured_institution = (structured.institution or "").lower()

    if ner_institution and structured_institution and ner_institution != structured_institution:
        return False

    return bool(
        ner_degree
        and structured_degree
        and ner_degree == structured_degree
        and (not ner_field or not structured_field or ner_field == structured_field)
    )


def _filter_redundant_ner_claims(claims: Sequence[Claim]) -> list[Claim]:
    """Drop NER rows that duplicate or degrade structured experience/education claims."""
    structured_experience = [
        claim.value
        for claim in claims
        if claim.field == "experience"
        and claim.method is ExtractionMethod.STRUCTURED_PARSE
        and isinstance(claim.value, ExperienceEntry)
    ]
    structured_education = [
        claim.value
        for claim in claims
        if claim.field == "education"
        and claim.method is ExtractionMethod.STRUCTURED_PARSE
        and isinstance(claim.value, EducationEntry)
    ]

    filtered: list[Claim] = []
    for claim in claims:
        if claim.method is not ExtractionMethod.NER:
            filtered.append(claim)
            continue
        if claim.field == "experience" and isinstance(claim.value, ExperienceEntry):
            entry = claim.value
            if _is_low_quality_ner_experience(entry):
                continue
            if any(
                _is_ner_experience_redundant(entry, structured)
                for structured in structured_experience
            ):
                continue
        if (
            claim.field == "education"
            and isinstance(claim.value, EducationEntry)
            and claim.value.institution is None
            and claim.value.degree is not None
            and any(
                _is_ner_education_redundant(claim.value, structured)
                for structured in structured_education
            )
        ):
            continue
        filtered.append(claim)
    return filtered


def _claims_from_entities(entities: Sequence[_GlinerEntity]) -> list[Claim]:
    """Convert GLiNER entities into claims, pairing nearby related spans."""
    ordered = sorted(entities, key=lambda entity: (entity["start"], entity["end"], entity["label"]))
    claims: list[Claim] = []
    method = ExtractionMethod.NER

    titles = [entity for entity in ordered if entity["label"].strip().lower() == "job title"]
    organizations = _usable_organizations([
        entity for entity in ordered if entity["label"].strip().lower() == "organization"
    ])
    universities = [entity for entity in ordered if entity["label"].strip().lower() == "university"]
    degrees = [entity for entity in ordered if entity["label"].strip().lower() == "degree"]

    used_orgs: set[int] = set()
    used_unis: set[int] = set()
    used_degrees: set[int] = set()
    used_titles: set[int] = set()

    for title_index, title in enumerate(titles):
        org, org_index = _closest_entity(title, organizations, used_orgs)
        title_text = title["text"].strip()
        raw = title["text"]
        if org is not None and org_index is not None:
            used_orgs.add(org_index)
            used_titles.add(title_index)
            raw = f"{title_text} @ {org['text'].strip()}"
            claims.append(_resume_claim(
                "experience",
                ExperienceEntry(title=title_text, company=org["text"].strip()),
                raw,
                method,
            ))
        else:
            used_titles.add(title_index)

    for uni_index, university in enumerate(universities):
        degree, degree_index = _closest_entity(university, degrees, used_degrees)
        institution = university["text"].strip()
        raw = university["text"]
        if degree is not None and degree_index is not None:
            used_degrees.add(degree_index)
            used_unis.add(uni_index)
            degree_text = degree["text"].strip()
            parsed_degree, field = _education_from_degree_text(degree_text)
            end_year = _education_year(degree_text)
            raw = f"{degree_text} @ {institution}"
            claims.append(_resume_claim(
                "education",
                EducationEntry(
                    institution=institution,
                    degree=parsed_degree,
                    field=field,
                    end_year=end_year,
                ),
                raw,
                method,
            ))
        else:
            used_unis.add(uni_index)

    for entity in ordered:
        label = entity["label"].strip().lower()
        if label in {"job title", "organization", "university", "degree"}:
            continue
        claim = _claim_from_entity(entity)
        if claim is not None:
            claims.append(claim)

    return claims


def _claim_from_entity(entity: _GlinerEntity) -> Claim | None:
    """Map one GLiNER entity to a resume claim, or None when the label is unknown."""
    raw = entity["text"]
    cleaned = raw.strip()
    if not cleaned:
        return None

    label = entity["label"].strip().lower()
    method = ExtractionMethod.NER

    if label == "person":
        return _resume_claim("full_name", cleaned, raw, method)
    if label == "certification":
        return _resume_claim("certifications", cleaned, raw, method)
    if label == "location":
        return _resume_claim("location", _location_from_text(cleaned), raw, method)
    return None

