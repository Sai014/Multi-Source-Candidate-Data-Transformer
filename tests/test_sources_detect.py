"""Tests for source detection, routing, and isolated extraction."""

from __future__ import annotations

from pathlib import Path

from app.domain.enums import ExtractionMethod, SourceType
from app.domain.models import Claim
from app.sources.detect import (
    QuarantineRecord,
    extract_isolated,
    find_adapter,
    ingest_paths,
)


class GoodAdapter:
    """Adapter that handles ``.good`` files and emits one claim."""

    source_type = SourceType.ATS

    def can_handle(self, path: Path) -> bool:
        return path.suffix == ".good"

    def extract(self, path: Path) -> list[Claim]:
        return [
            Claim(
                field="full_name",
                value=path.stem,
                source=self.source_type,
                method=ExtractionMethod.DIRECT_MAP,
                raw=path.name,
            )
        ]


class RaisingAdapter:
    """Adapter that handles ``.bad`` files but always fails to extract."""

    source_type = SourceType.RESUME

    def can_handle(self, path: Path) -> bool:
        return path.suffix == ".bad"

    def extract(self, path: Path) -> list[Claim]:
        raise ValueError("boom while parsing")


class RaisingCanHandleAdapter:
    """Adapter whose capability check itself raises."""

    source_type = SourceType.NOTES

    def can_handle(self, path: Path) -> bool:
        raise RuntimeError("detector exploded")

    def extract(self, path: Path) -> list[Claim]:  # pragma: no cover - never reached
        return []


def test_raising_adapter_is_quarantined_with_reason() -> None:
    """A failing extract becomes a quarantine record carrying the failure reason."""
    adapter = RaisingAdapter()
    outcome = extract_isolated(adapter, Path("resume.bad"))
    assert isinstance(outcome, QuarantineRecord)
    assert outcome.source is SourceType.RESUME
    assert outcome.path == "resume.bad"
    assert "boom while parsing" in outcome.reason
    assert "ValueError" in outcome.reason


def test_bad_source_does_not_stop_other_sources() -> None:
    """One quarantined source must not block extraction of the others."""
    result = ingest_paths(
        [Path("first.bad"), Path("second.good"), Path("third.good")],
        adapters=[RaisingAdapter(), GoodAdapter()],
    )
    assert [c.value for c in result.ledger.claims] == ["second", "third"]
    assert len(result.quarantined) == 1
    assert result.quarantined[0].path == "first.bad"
    assert result.quarantined[0].source is SourceType.RESUME


def test_unrecognized_path_is_quarantined_as_no_adapter() -> None:
    """A path no adapter handles is quarantined with source=None."""
    result = ingest_paths([Path("mystery.xyz")], adapters=[GoodAdapter()])
    assert len(result.ledger) == 0
    assert len(result.quarantined) == 1
    assert result.quarantined[0].source is None
    assert result.quarantined[0].reason == "no_adapter"


def test_find_adapter_returns_first_match() -> None:
    """Routing picks the first adapter that can handle the path."""
    good = GoodAdapter()
    assert find_adapter(Path("x.good"), [good]) is good
    assert find_adapter(Path("x.bad"), [good]) is None


def test_raising_can_handle_is_treated_as_cannot_handle() -> None:
    """A raising capability check is skipped, not fatal."""
    result = ingest_paths(
        [Path("x.good")],
        adapters=[RaisingCanHandleAdapter(), GoodAdapter()],
    )
    assert [c.value for c in result.ledger.claims] == ["x"]
    assert len(result.quarantined) == 0
