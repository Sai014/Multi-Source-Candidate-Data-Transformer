"""Resume source adapter."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

from app.domain.enums import SourceType
from app.domain.models import Claim
from app.sources.base import register_adapter
from app.sources.resume.extractors import (
    GlinerExtractor,
    ProseExtractor,
    ResumeExtractor,
    SectionExtractor,
)
from app.sources.resume.gliner import _filter_redundant_ner_claims
from app.sources.resume.readers import read_resume_text
from app.sources.resume.text import _normalize_resume_text

logger = logging.getLogger(__name__)


class ResumeAdapter:
    """Adapter that reads a resume file and runs every extraction strategy."""

    source_type = SourceType.RESUME
    _SUFFIXES: ClassVar[set[str]] = {".txt", ".pdf", ".docx"}

    def __init__(self, extractors: Sequence[ResumeExtractor] | None = None) -> None:
        self._extractors: tuple[ResumeExtractor, ...] = (
            (ProseExtractor(), SectionExtractor(), GlinerExtractor())
            if extractors is None
            else tuple(extractors)
        )

    def can_handle(self, path: Path) -> bool:
        """Handle resume document extensions."""
        return path.suffix.lower() in self._SUFFIXES

    def extract(self, path: Path) -> list[Claim]:
        """Read the resume and collect claims from every strategy.

        Reading failures propagate (so a garbage file is quarantined); a strategy
        that trips on odd formatting is isolated and degrades to fewer claims.
        """
        text = _normalize_resume_text(read_resume_text(path))
        claims: list[Claim] = []
        for extractor in self._extractors:
            try:
                claims.extend(extractor.extract(text))
            except Exception:
                logger.exception("%s extraction failed", type(extractor).__name__)
                continue
        return _filter_redundant_ner_claims(claims)


register_adapter(ResumeAdapter())
