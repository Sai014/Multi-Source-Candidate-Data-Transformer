"""Domain models: claims, the canonical profile, and runtime config.

Design spine:

- Every extracted value is a :class:`Claim` (who said it, how it was obtained) -
  not a fact.
- :class:`CanonicalProfile` is the adjudicated verdict over claims.
- A hard wall separates the canonical record from the config-driven projection;
  :class:`Config` is *data* the engine interprets, never a code change.

All shapes are Pydantic v2 models with precise types - no ``typing.Any``.
"""

from __future__ import annotations

from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import ExtractionMethod, OnMissing, SourceType

# --------------------------------------------------------------------------- #
# Structured value models                                                     #
# --------------------------------------------------------------------------- #


class Location(BaseModel):
    """A geographic location. ``country`` is ISO-3166 alpha-2."""

    city: str | None = None
    region: str | None = None
    country: str | None = None


class Links(BaseModel):
    """Outbound profile links for a candidate."""

    linkedin: str | None = None
    github: str | None = None
    portfolio: str | None = None
    other: list[str] = Field(default_factory=list)


class ExperienceEntry(BaseModel):
    """A single work-history entry. ``start``/``end`` are ``YYYY-MM``."""

    company: str | None = None
    title: str | None = None
    start: str | None = None
    end: str | None = None
    summary: str | None = None


class EducationEntry(BaseModel):
    """A single education entry."""

    institution: str | None = None
    degree: str | None = None
    field: str | None = None
    end_year: int | None = None


class Skill(BaseModel):
    """A canonicalized skill with its fused confidence and contributing sources."""

    name: str
    confidence: float
    sources: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Claims                                                                      #
# --------------------------------------------------------------------------- #

ClaimValue: TypeAlias = (
    str | int | float | list[str] | ExperienceEntry | EducationEntry | Location | Links
)
"""Closed union of everything a single claim may assert. No ``Any``."""


class Claim(BaseModel):
    """One source's assertion about one canonical field.

    A claim is immutable once created: it is a record of what was said and how,
    so it must never be mutated in place.
    """

    model_config = ConfigDict(frozen=True)

    field: str
    value: ClaimValue
    source: SourceType
    method: ExtractionMethod
    raw: str | None
    normalized: ClaimValue | None = None
    normalize_ok: bool | None = None
    confidence: float | None = None


class Provenance(BaseModel):
    """A record of where a fused field's value came from and how."""

    field: str
    source: SourceType
    method: ExtractionMethod
    note: str | None = None


# --------------------------------------------------------------------------- #
# Canonical profile (fixed output schema)                                     #
# --------------------------------------------------------------------------- #


class CanonicalProfile(BaseModel):
    """The single, adjudicated profile per candidate (fixed schema)."""

    candidate_id: str
    full_name: str | None = None
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    location: Location = Field(default_factory=Location)
    links: Links = Field(default_factory=Links)
    headline: str | None = None
    years_experience: float | None = None
    skills: list[Skill] = Field(default_factory=list)
    experience: list[ExperienceEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)
    overall_confidence: float = 0.0


# --------------------------------------------------------------------------- #
# Runtime config (projection layer)                                           #
# --------------------------------------------------------------------------- #


class FieldSpec(BaseModel):
    """One requested output field in a runtime projection config.

    ``from_`` is exposed as ``"from"`` in JSON via field aliasing; it names the
    canonical path a value is sourced from when it differs from ``path``.
    """

    model_config = ConfigDict(populate_by_name=True)

    path: str
    from_: str | None = Field(default=None, alias="from")
    type: str
    required: bool = False
    normalize: str | None = None


class Config(BaseModel):
    """Runtime projection config: data the engine interprets."""

    fields: list[FieldSpec] = Field(default_factory=list)
    include_confidence: bool = False
    include_provenance: bool = False
    on_missing: OnMissing = OnMissing.NULL
