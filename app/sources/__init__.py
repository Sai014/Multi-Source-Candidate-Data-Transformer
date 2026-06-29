"""Source adapters and source-type detection."""

from app.sources.ats import ATS_FIELD_MAP, AtsAdapter
from app.sources.base import ADAPTER_REGISTRY, SourceAdapter, register_adapter
from app.sources.detect import (
    IngestResult,
    QuarantineRecord,
    extract_isolated,
    find_adapter,
    ingest_paths,
)
from app.sources.resume import (
    ProseExtractor,
    ResumeAdapter,
    ResumeExtractor,
    SectionExtractor,
)

__all__ = [
    "ADAPTER_REGISTRY",
    "ATS_FIELD_MAP",
    "AtsAdapter",
    "IngestResult",
    "ProseExtractor",
    "QuarantineRecord",
    "ResumeAdapter",
    "ResumeExtractor",
    "SectionExtractor",
    "SourceAdapter",
    "extract_isolated",
    "find_adapter",
    "ingest_paths",
    "register_adapter",
]
