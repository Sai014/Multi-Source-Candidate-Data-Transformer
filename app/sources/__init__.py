"""Source adapters and source-type detection."""

from app.sources.base import ADAPTER_REGISTRY, SourceAdapter, register_adapter
from app.sources.detect import (
    IngestResult,
    QuarantineRecord,
    extract_isolated,
    find_adapter,
    ingest_paths,
)

__all__ = [
    "ADAPTER_REGISTRY",
    "IngestResult",
    "QuarantineRecord",
    "SourceAdapter",
    "extract_isolated",
    "find_adapter",
    "ingest_paths",
    "register_adapter",
]
