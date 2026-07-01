"""End-to-end pipeline orchestration.

Wires the stages together: detect -> extract -> ledger -> resolve -> fuse ->
project -> validate. Extraction is the only impure step (it reads files); it is
isolated so a bad source becomes a :class:`~app.sources.detect.QuarantineRecord`
instead of crashing the run. No step throws on bad input.

Importing this module registers the built-in adapters via ``app.sources``.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, Field

from app.domain.models import CanonicalProfile, Config
from app.pipeline.fuse import fuse
from app.pipeline.project import Projection, ProjectionReport, project
from app.pipeline.resolve import cluster
from app.pipeline.validate import validate_view
from app.sources import ADAPTER_REGISTRY, SourceAdapter
from app.sources.detect import QuarantineRecord, ingest_paths


class RunResult(BaseModel):
    """The aggregate outcome of a pipeline run.

    ``profiles``, ``projections``, and ``reports`` are index-aligned per candidate.
    ``quarantined`` collects every source that could not be ingested.
    """

    profiles: list[CanonicalProfile] = Field(default_factory=list)
    projections: list[Projection] = Field(default_factory=list)
    reports: list[ProjectionReport] = Field(default_factory=list)
    quarantined: list[QuarantineRecord] = Field(default_factory=list)


def run(
    input_paths: Sequence[Path],
    config: Config,
    adapters: Sequence[SourceAdapter] | None = None,
) -> RunResult:
    """Run the full pipeline over ``input_paths`` under ``config``.

    Returns one profile, projection, and report per resolved candidate, plus the
    quarantine records for any sources that failed. Pure given fixed file contents
    and adapters; never raises on bad input.
    """
    active = ADAPTER_REGISTRY if adapters is None else adapters
    ingest = ingest_paths(input_paths, active)
    records = [list(record) for record in ingest.records]
    quarantined = list(ingest.quarantined)

    profiles: list[CanonicalProfile] = []
    projections: list[Projection] = []
    reports: list[ProjectionReport] = []

    for candidate_claims in cluster(records):
        profile = fuse(candidate_claims)
        projection, projection_report = project(profile, config)
        schema_report = validate_view(projection.values, config)
        merged = ProjectionReport(
            violations=[*projection_report.violations, *schema_report.violations]
        )
        profiles.append(profile)
        projections.append(projection)
        reports.append(merged)

    return RunResult(
        profiles=profiles,
        projections=projections,
        reports=reports,
        quarantined=quarantined,
    )


__all__ = ["RunResult", "run"]
