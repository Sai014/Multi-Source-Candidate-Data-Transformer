"""Domain enumerations.

String-valued enums so they serialize to stable, human-readable JSON and match
the fixed confidence-prior tables (e.g. ``SourceType.ATS == "ats"``).
"""

from __future__ import annotations

from enum import StrEnum


class SourceType(StrEnum):
    """Where a claim originated."""

    ATS = "ats"
    RESUME = "resume"
    NOTES = "notes"
    GITHUB = "github"


class ExtractionMethod(StrEnum):
    """How a value was obtained from a source."""

    DIRECT_MAP = "direct_map"
    STRUCTURED_PARSE = "structured_parse"
    REGEX_PROSE = "regex_prose"
    NLP_INFERRED = "nlp_inferred"


class OnMissing(StrEnum):
    """Projection policy when a requested value is absent."""

    NULL = "null"
    OMIT = "omit"
    ERROR = "error"
