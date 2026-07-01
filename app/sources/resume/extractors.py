"""Resume extraction strategies (section, prose, GLiNER)."""

from __future__ import annotations

import logging
from typing import Protocol

from app.domain.enums import ExtractionMethod
from app.domain.models import Claim
from app.sources.resume.gliner import _claims_from_entities, _predict_entities
from app.sources.resume.text import (
    _HEADER_KEY,
    _YEARS_RE,
    _dedupe,
    _find_emails,
    _find_links,
    _find_phones,
    _header_full_name,
    _normalize_resume_text,
    _parse_education,
    _parse_experience,
    _parse_skills,
    _resume_claim,
    _split_sections,
)

logger = logging.getLogger(__name__)


class ResumeExtractor(Protocol):
    """A strategy that turns resume text into claims."""

    def extract(self, text: str) -> list[Claim]:
        """Return the claims this strategy can derive from ``text``."""
        ...


class SectionExtractor:
    """Heading-based structured parsing (higher-trust structured parse)."""

    _method = ExtractionMethod.STRUCTURED_PARSE

    def extract(self, text: str) -> list[Claim]:
        claims: list[Claim] = []
        lines = _normalize_resume_text(text).splitlines()
        nonempty = [line.strip() for line in lines if line.strip()]

        full_name = _header_full_name(nonempty)
        if full_name:
            claims.append(_resume_claim("full_name", full_name, full_name, self._method))

        sections = _split_sections(lines)
        header_text = "\n".join(sections.get(_HEADER_KEY, []))
        for email in _dedupe(_find_emails(header_text)):
            claims.append(_resume_claim("emails", email, email, self._method))
        for phone in _dedupe(_find_phones(header_text)):
            claims.append(_resume_claim("phones", phone, phone, self._method))
        links = _find_links(header_text)
        if links is not None:
            raw = "; ".join(filter(None, (links.linkedin, links.github)))
            claims.append(_resume_claim("links", links, raw, self._method))

        for skill in _parse_skills(sections.get("skills", [])):
            claims.append(_resume_claim("skills", skill, skill, self._method))
        for experience_entry, raw in _parse_experience(sections.get("experience", [])):
            claims.append(_resume_claim("experience", experience_entry, raw, self._method))
        for education_entry, raw in _parse_education(sections.get("education", [])):
            claims.append(_resume_claim("education", education_entry, raw, self._method))

        return claims


class ProseExtractor:
    """Whole-document regex extraction (lower-trust prose parse)."""

    _method = ExtractionMethod.REGEX_PROSE

    def extract(self, text: str) -> list[Claim]:
        claims: list[Claim] = []
        for email in _dedupe(_find_emails(text)):
            claims.append(_resume_claim("emails", email, email, self._method))
        for phone in _dedupe(_find_phones(text)):
            claims.append(_resume_claim("phones", phone, phone, self._method))
        years_match = _YEARS_RE.search(text)
        if years_match:
            phrase = years_match.group(0).strip()
            claims.append(_resume_claim("years_experience", phrase, phrase, self._method))
        return claims


class GlinerExtractor:
    """Semantic entity extraction over the full resume text (NER)."""

    _method = ExtractionMethod.NER

    def extract(self, text: str) -> list[Claim]:
        if not text.strip():
            return []
        try:
            normalized = _normalize_resume_text(text)
            entities = _predict_entities(normalized)
        except Exception:
            logger.exception("GLiNER entity prediction failed")
            return []
        return _claims_from_entities(entities)

