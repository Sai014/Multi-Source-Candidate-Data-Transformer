"""Schema validation of a projected view against its config.

The schema is derived purely from each field's declared ``type`` and ``required``
flag - the engine never branches on a specific field name. Validation produces a
:class:`~app.pipeline.project.ProjectionReport`; on an ``on_missing=error`` policy
the projection has already omitted the offending field, so validation only confirms
that what *was* emitted conforms.
"""

from __future__ import annotations

from app.domain.models import Config
from app.pipeline.project import (
    ProjectedView,
    ProjectionReport,
    ResolvedValue,
    Violation,
)


def _is_string_list(value: ResolvedValue) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _type_ok(value: ResolvedValue, type_token: str) -> bool:
    """Check a resolved value against a declared type token.

    ``None`` always passes here: presence/absence is governed by ``required`` and
    the missing-value policy, not by the type schema. Unknown type tokens impose no
    constraint (forward-compatible).
    """
    if value is None:
        return True
    token = type_token.strip().lower()
    if token == "string":
        return isinstance(value, str)
    if token in {"string[]", "array", "list"}:
        return _is_string_list(value)
    if token in {"number", "float"}:
        return isinstance(value, int | float) and not isinstance(value, bool)
    if token in {"integer", "int"}:
        return isinstance(value, int) and not isinstance(value, bool)
    if token == "boolean":
        return isinstance(value, bool)
    if token == "object":
        return isinstance(value, dict)
    return True


def validate_view(view: ProjectedView, config: Config) -> ProjectionReport:
    """Validate a projected view (bare values) against the config-derived schema.

    Records a ``missing_required`` violation when a required field is absent or
    null, and a ``type_mismatch`` violation when a present value does not satisfy
    its declared type.
    """
    report = ProjectionReport()
    for spec in config.fields:
        value = view.get(spec.path)
        if value is None:
            if spec.required:
                report.violations.append(
                    Violation(path=spec.path, reason="missing_required")
                )
            continue
        if not _type_ok(value, spec.type):
            report.violations.append(
                Violation(path=spec.path, reason="type_mismatch", detail=spec.type)
            )
    return report
