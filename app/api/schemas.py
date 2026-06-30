"""API/CLI response shapes built from a pipeline :class:`RunResult`.

These models live outside the FastAPI app so the CLI can import and emit byte-for-
byte identical output without pulling in the web framework. The transformation
logic stays in the pure core; this module only reshapes an existing result.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.domain.models import CanonicalProfile
from app.pipeline.orchestrate import RunResult
from app.pipeline.project import ProjectedMeta, ProjectedView, ProjectionReport
from app.sources.detect import QuarantineRecord


class RunSummary(BaseModel):
    """At-a-glance counts for one transform run."""

    profile_count: int
    quarantined_count: int
    overall_confidence: float | None = None


class TransformResponse(BaseModel):
    """The full response for a ``/transform`` call (and the CLI's output).

    ``projected`` holds bare field values (the requested schema). ``projected_meta``
    carries per-field confidence/provenance only when those toggles are enabled in
    the config; it is an empty map per candidate otherwise.
    """

    profiles: list[CanonicalProfile]
    projected: list[ProjectedView]
    projected_meta: list[dict[str, ProjectedMeta]]
    quarantined: list[QuarantineRecord]
    reports: list[ProjectionReport]
    summary: RunSummary


def _overall_confidence(profiles: list[CanonicalProfile]) -> float | None:
    """Mean overall-confidence across resolved profiles, or ``None`` when empty."""
    if not profiles:
        return None
    return sum(profile.overall_confidence for profile in profiles) / len(profiles)


def build_transform_response(result: RunResult) -> TransformResponse:
    """Map a pipeline :class:`RunResult` into the public response shape."""
    summary = RunSummary(
        profile_count=len(result.profiles),
        quarantined_count=len(result.quarantined),
        overall_confidence=_overall_confidence(result.profiles),
    )
    return TransformResponse(
        profiles=result.profiles,
        projected=[projection.values for projection in result.projections],
        projected_meta=[projection.meta for projection in result.projections],
        quarantined=result.quarantined,
        reports=result.reports,
        summary=summary,
    )
