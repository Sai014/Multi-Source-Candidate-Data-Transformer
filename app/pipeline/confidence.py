"""Confidence priors and the small arithmetic used during adjudication.

These tables are fixed and tunable; the scores are ordinal (a defensible ranking),
not calibrated probabilities. The per-claim score is ``source_trust x
method_reliability``; corroboration combines via noisy-OR.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.domain.enums import ExtractionMethod, SourceType

SOURCE_TRUST: dict[str, float] = {
    "ats": 0.90,
    "github": 0.70,
    "resume_section": 0.75,
    "resume_prose": 0.55,
    "notes": 0.40,
}

METHOD_RELIABILITY: dict[str, float] = {
    "direct_map": 1.0,
    "structured_parse": 0.8,
    "regex_prose": 0.6,
    "ner": 0.5,
}

SOURCE_PRIORITY: dict[str, int] = {
    "ats": 0,
    "github": 1,
    "resume_section": 2,
    "resume_prose": 3,
    "notes": 4,
}

TAU = 0.5
NOISY_OR_CAP = 0.99
DISAGREEMENT_PENALTY = 0.1
HIGH_TRUST_THRESHOLD = 0.7


def trust_key(source: SourceType, method: ExtractionMethod) -> str:
    """Derive the SOURCE_TRUST key from a claim's source and method.

    Resume claims split into the structured ("resume_section") and prose
    ("resume_prose") trust buckets based on extraction method.
    """
    if source is SourceType.ATS:
        return "ats"
    if source is SourceType.GITHUB:
        return "github"
    if source is SourceType.NOTES:
        return "notes"
    if method is ExtractionMethod.STRUCTURED_PARSE:
        return "resume_section"
    return "resume_prose"


def claim_confidence(source: SourceType, method: ExtractionMethod) -> float:
    """Per-claim confidence c = source_trust x method_reliability."""
    return SOURCE_TRUST[trust_key(source, method)] * METHOD_RELIABILITY[method.value]


def source_priority(source: SourceType, method: ExtractionMethod) -> int:
    """Fixed tie-break priority (lower wins) derived from the trust bucket."""
    return SOURCE_PRIORITY[trust_key(source, method)]


def noisy_or(confidences: Iterable[float]) -> float:
    """Combine independent corroborating confidences: 1 - prod(1 - c), capped."""
    product = 1.0
    for confidence in confidences:
        product *= 1.0 - confidence
    return min(NOISY_OR_CAP, 1.0 - product)
