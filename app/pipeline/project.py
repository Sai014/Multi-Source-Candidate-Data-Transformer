"""Config-driven projection of a canonical profile.

The canonical record and the projection are separated by a hard wall: this engine
*interprets* a :class:`~app.domain.models.Config` as data and never branches on a
specific field name. It resolves each requested field from the profile, optionally
re-normalizes it through the shared registry, applies the missing-value policy, and
attaches confidence/provenance when toggled on.

A null or empty config yields the full default canonical projection.
"""

from __future__ import annotations

import re
from typing import Literal, TypeAlias

from pydantic import BaseModel, Field
from typing_extensions import TypeAliasType

from app.domain.enums import OnMissing
from app.domain.models import CanonicalProfile, Config, FieldSpec, Provenance
from app.normalize import REGISTRY, normalize

# --------------------------------------------------------------------------- #
# Resolver output: a closed, JSON-like union (no ``Any``)                     #
# --------------------------------------------------------------------------- #

# A named recursive alias (PEP 695 style) so both mypy and Pydantic can build a
# finite schema for the self-referential union - an implicit alias recurses forever.
ResolvedValue = TypeAliasType(
    "ResolvedValue",
    "str | int | float | bool | None | list[ResolvedValue] | dict[str, ResolvedValue]",
)
"""Everything a resolved path may yield: scalars, lists, or nested maps."""


class ProjectedValue(BaseModel):
    """One projected output field, with optional confidence/provenance metadata."""

    value: ResolvedValue = None
    confidence: float | None = None
    provenance: list[Provenance] | None = None


ProjectedView: TypeAlias = dict[str, ProjectedValue]


ViolationReason = Literal[
    "missing_required", "missing", "normalize_failed", "type_mismatch"
]


class Violation(BaseModel):
    """A single projection or validation problem for one requested field."""

    path: str
    reason: ViolationReason
    detail: str | None = None


class ProjectionReport(BaseModel):
    """The collected violations for one projection."""

    violations: list[Violation] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when the projection produced no violations."""
        return not self.violations


# --------------------------------------------------------------------------- #
# Normalizer-token aliases (config tokens -> registry names)                  #
# --------------------------------------------------------------------------- #

_NORMALIZE_ALIASES: dict[str, str] = {
    "phone": "phone_e164",
    "e164": "phone_e164",
    "canonical": "skill_canonical",
    "skill": "skill_canonical",
    "country": "country_iso2",
    "date": "date_ym",
    "url": "url_link",
    "text": "text_unicode",
    "years": "years_experience",
}


def _normalizer_name(token: str) -> str:
    """Map a config normalize token to a registry name (pass-through if unknown)."""
    return _NORMALIZE_ALIASES.get(token.strip().lower(), token)


# --------------------------------------------------------------------------- #
# JSON-like coercion                                                          #
# --------------------------------------------------------------------------- #


def _to_resolved(value: object) -> ResolvedValue:
    """Coerce an arbitrary dumped value into the closed ``ResolvedValue`` union."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list):
        return [_to_resolved(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_resolved(item) for key, item in value.items()}
    return str(value)


# --------------------------------------------------------------------------- #
# Path resolution: dotted paths, array index, map-over-list                   #
# --------------------------------------------------------------------------- #

_INDEX_RE = re.compile(r"^(.+)\[(\d+)\]$")
_SegmentOp = Literal["field", "index", "map"]


def _parse_segment(segment: str) -> tuple[str, _SegmentOp, int]:
    """Split ``name``, ``name[0]``, or ``name[]`` into (name, op, index)."""
    if segment.endswith("[]"):
        return segment[:-2], "map", 0
    match = _INDEX_RE.match(segment)
    if match is not None:
        return match.group(1), "index", int(match.group(2))
    return segment, "field", 0


def _field(value: ResolvedValue, name: str) -> tuple[bool, ResolvedValue]:
    """Read ``name`` from a mapping value; (False, None) if absent or non-mapping."""
    if isinstance(value, dict) and name in value:
        return True, value[name]
    return False, None


def _resolve_path(root: ResolvedValue, path: str) -> tuple[bool, ResolvedValue]:
    """Resolve a dotted/indexed/mapped path against ``root``.

    Returns ``(found, value)``. ``found`` is False whenever any segment cannot be
    navigated (absent key, out-of-range index, or a type that cannot be indexed),
    so callers can apply the missing-value policy without exceptions.
    """
    current: ResolvedValue = root
    mapping = False
    for segment in path.split("."):
        name, op, index = _parse_segment(segment)
        if mapping:
            if not isinstance(current, list):
                return False, None
            collected: list[ResolvedValue] = []
            for item in current:
                found, field_value = _field(item, name)
                if not found:
                    return False, None
                collected.append(field_value)
            current = collected
        else:
            found, field_value = _field(current, name)
            if not found:
                return False, None
            current = field_value

        if op == "index":
            if mapping or not isinstance(current, list) or index >= len(current):
                return False, None
            current = current[index]
        elif op == "map":
            if not isinstance(current, list):
                return False, None
            mapping = True

    return True, current


def _root_field(path: str) -> str:
    """The canonical top-level field a path is sourced from (``emails[0]`` -> ``emails``)."""
    return re.split(r"[.\[]", path, maxsplit=1)[0]


# --------------------------------------------------------------------------- #
# Per-field normalization                                                     #
# --------------------------------------------------------------------------- #


def _norm_scalar(name: str, raw: str) -> ResolvedValue | None:
    """Run one registry normalizer on a string, narrowing the result to a scalar."""
    result = normalize(name, raw)
    if not result.ok:
        return None
    candidate = result.value
    if candidate is None or isinstance(candidate, str | int | float | bool):
        return candidate
    return None


def _apply_normalize(value: ResolvedValue, token: str) -> ResolvedValue:
    """Re-normalize ``value`` via the named registry function.

    Unknown tokens are a no-op (the original value is returned). Scalars are
    normalized directly; lists are normalized element-wise, dropping items that
    fail to normalize.
    """
    name = _normalizer_name(token)
    if name not in REGISTRY:
        return value
    if isinstance(value, str):
        return _norm_scalar(name, value)
    if isinstance(value, list):
        out: list[ResolvedValue] = []
        for item in value:
            if isinstance(item, str):
                normalized = _norm_scalar(name, item)
                if normalized is not None:
                    out.append(normalized)
            else:
                out.append(item)
        return out
    return value


# --------------------------------------------------------------------------- #
# Projection                                                                  #
# --------------------------------------------------------------------------- #


def _make_projected(
    value: ResolvedValue,
    profile: CanonicalProfile,
    config: Config,
    from_path: str,
) -> ProjectedValue:
    """Wrap a resolved value, attaching toggled confidence/provenance."""
    confidence = profile.overall_confidence if config.include_confidence else None
    provenance: list[Provenance] | None = None
    if config.include_provenance:
        root = _root_field(from_path)
        provenance = [p for p in profile.provenance if p.field == root]
    return ProjectedValue(value=value, confidence=confidence, provenance=provenance)


def _default_projection(tree: ResolvedValue) -> ProjectedView:
    """Project every canonical top-level field as-is (no remap, no toggles)."""
    view: ProjectedView = {}
    if isinstance(tree, dict):
        for key, value in tree.items():
            view[key] = ProjectedValue(value=value)
    return view


def _project_field(
    spec: FieldSpec,
    tree: ResolvedValue,
    profile: CanonicalProfile,
    config: Config,
    view: ProjectedView,
    report: ProjectionReport,
) -> None:
    """Resolve, normalize, and place one requested field, recording violations."""
    from_path = spec.from_ or spec.path
    found, value = _resolve_path(tree, from_path)
    is_missing = (not found) or value is None

    if is_missing:
        if spec.required:
            report.violations.append(
                Violation(path=spec.path, reason="missing_required", detail=from_path)
            )
            return
        if config.on_missing is OnMissing.OMIT:
            return
        if config.on_missing is OnMissing.ERROR:
            report.violations.append(
                Violation(path=spec.path, reason="missing", detail=from_path)
            )
            return
        view[spec.path] = _make_projected(None, profile, config, from_path)
        return

    if spec.normalize is not None:
        normalized = _apply_normalize(value, spec.normalize)
        if normalized is None:
            if spec.required:
                report.violations.append(
                    Violation(
                        path=spec.path,
                        reason="normalize_failed",
                        detail=spec.normalize,
                    )
                )
                return
            value = None
        else:
            value = normalized

    view[spec.path] = _make_projected(value, profile, config, from_path)


def project(
    profile: CanonicalProfile, config: Config
) -> tuple[ProjectedView, ProjectionReport]:
    """Project ``profile`` according to ``config``; pure and deterministic.

    With no configured fields, the full default canonical projection is returned.
    Otherwise each :class:`~app.domain.models.FieldSpec` is resolved, optionally
    normalized, and emitted per the ``on_missing`` policy.
    """
    tree = _to_resolved(profile.model_dump(mode="json"))
    report = ProjectionReport()

    if not config.fields:
        return _default_projection(tree), report

    view: ProjectedView = {}
    for spec in config.fields:
        _project_field(spec, tree, profile, config, view, report)
    return view, report
