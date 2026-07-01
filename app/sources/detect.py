"""Source detection, routing, and isolated extraction.

Each input path is routed to the first adapter that can handle it. Extraction is
wrapped in isolation: any failure (missing, garbage, or malformed source) becomes
a :class:`QuarantineRecord` with a structured reason, and the run continues -
a bad source never crashes the pipeline and never blocks other sources.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from app.domain.enums import SourceType
from app.domain.models import Claim
from app.sources.base import ADAPTER_REGISTRY, SourceAdapter

if TYPE_CHECKING:
    from app.pipeline.ledger import ClaimLedger


class QuarantineRecord(BaseModel):
    """A structured record of a source that could not be ingested.

    ``source`` is the responsible adapter's type, or ``None`` when no adapter
    recognized the input.
    """

    source: SourceType | None
    path: str
    reason: str


@dataclass(frozen=True)
class IngestResult:
    """Outcome of ingesting a batch of source paths."""

    ledger: ClaimLedger
    records: tuple[tuple[Claim, ...], ...]
    quarantined: tuple[QuarantineRecord, ...]


def _can_handle(adapter: SourceAdapter, path: Path) -> bool:
    """Safely ask an adapter whether it handles ``path`` (a raising check = no)."""
    try:
        return adapter.can_handle(path)
    except Exception:
        return False


def find_adapter(path: Path, adapters: Sequence[SourceAdapter]) -> SourceAdapter | None:
    """Return the first adapter that can handle ``path``, or ``None``."""
    for adapter in adapters:
        if _can_handle(adapter, path):
            return adapter
    return None


def extract_isolated(
    adapter: SourceAdapter, path: Path
) -> list[Claim] | QuarantineRecord:
    """Run ``adapter.extract`` under isolation.

    Returns the extracted claims, or a :class:`QuarantineRecord` if extraction
    raised for any reason. Never propagates an exception.
    """
    try:
        return adapter.extract(path)
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        return QuarantineRecord(source=adapter.source_type, path=str(path), reason=reason)


def ingest_paths(
    paths: Iterable[Path],
    adapters: Sequence[SourceAdapter] | None = None,
) -> IngestResult:
    """Route and extract each path into a single ledger, quarantining failures.

    Adapters default to the global registry but may be passed explicitly (keeping
    the function deterministic and easy to test).
    """
    from app.pipeline.ledger import ClaimLedger

    active = ADAPTER_REGISTRY if adapters is None else adapters
    ledger = ClaimLedger()
    records: list[list[Claim]] = []
    quarantined: list[QuarantineRecord] = []

    for path in paths:
        adapter = find_adapter(path, active)
        if adapter is None:
            quarantined.append(
                QuarantineRecord(source=None, path=str(path), reason="no_adapter")
            )
            continue
        outcome = extract_isolated(adapter, path)
        if isinstance(outcome, QuarantineRecord):
            quarantined.append(outcome)
        elif outcome:
            ledger.extend(outcome)
            records.append(outcome)

    return IngestResult(
        ledger=ledger,
        records=tuple(tuple(record) for record in records),
        quarantined=tuple(quarantined),
    )
