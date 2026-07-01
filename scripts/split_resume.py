"""One-off helper to split app/sources/resume.py into a package."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "app" / "sources" / "resume"
lines = (ROOT / "app" / "sources" / "resume.py").read_text(encoding="utf-8").splitlines(keepends=True)

COMMON_IMPORTS = '''from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from typing import ClassVar

from app.domain.enums import ExtractionMethod, SourceType
from app.domain.models import Claim, ClaimValue, EducationEntry, ExperienceEntry, Links, Location

logger = logging.getLogger(__name__)

'''

GLINER_IMPORTS = '''from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from functools import lru_cache
from typing import Protocol, TypedDict, cast

from app.domain.enums import ExtractionMethod, SourceType
from app.domain.models import Claim, ClaimValue, EducationEntry, ExperienceEntry, Location
from app.sources.resume.text import (
    _education_year,
    _location_from_text,
    _looks_like_degree,
    _resume_claim,
    _split_degree_field,
)

logger = logging.getLogger(__name__)

'''

EXTRACTOR_IMPORTS = '''from __future__ import annotations

from typing import Protocol

from app.domain.enums import ExtractionMethod
from app.domain.models import Claim
from app.sources.resume.gliner import _claims_from_entities, _predict_entities
from app.sources.resume.text import (
    _HEADER_KEY,
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
    _YEARS_RE,
)

'''

READER_IMPORTS = '''from __future__ import annotations

from pathlib import Path

'''

ADAPTER_IMPORTS = '''from __future__ import annotations

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

'''


def main() -> None:
    PKG.mkdir(exist_ok=True)
    text_body = "".join(lines[40:497])
    gliner_body = "".join(lines[503:888])
    extractor_body = "".join(lines[895:971])
    reader_body = "".join(lines[978:1007]).replace("_read_text", "read_resume_text")
    adapter_body = "".join(lines[1010:1044]).replace("_read_text(path)", "read_resume_text(path)")

    (PKG / "text.py").write_text(
        '"""Shared resume text normalization and structured parsing helpers."""\n\n'
        + COMMON_IMPORTS
        + text_body,
        encoding="utf-8",
    )
    (PKG / "gliner.py").write_text(
        '"""GLiNER NER extraction for resume documents."""\n\n' + GLINER_IMPORTS + gliner_body,
        encoding="utf-8",
    )
    (PKG / "extractors.py").write_text(
        '"""Resume extraction strategies (section, prose, GLiNER)."""\n\n'
        + EXTRACTOR_IMPORTS
        + extractor_body,
        encoding="utf-8",
    )
    (PKG / "readers.py").write_text(
        '"""Read resume text from PDF, DOCX, and plain-text files."""\n\n'
        + READER_IMPORTS
        + reader_body,
        encoding="utf-8",
    )
    (PKG / "adapter.py").write_text(
        '"""Resume source adapter."""\n\n' + ADAPTER_IMPORTS + adapter_body + "\n",
        encoding="utf-8",
    )
    (PKG / "__init__.py").write_text(
        '''"""Resume adapter with complementary extraction strategies."""

from app.sources.resume.adapter import ResumeAdapter
from app.sources.resume.extractors import (
    GlinerExtractor,
    ProseExtractor,
    ResumeExtractor,
    SectionExtractor,
)
from app.sources.resume.gliner import preload_gliner_model

__all__ = [
    "GlinerExtractor",
    "ProseExtractor",
    "ResumeAdapter",
    "ResumeExtractor",
    "SectionExtractor",
    "preload_gliner_model",
]
''',
        encoding="utf-8",
    )
    print(f"Wrote resume package under {PKG}")


if __name__ == "__main__":
    main()
