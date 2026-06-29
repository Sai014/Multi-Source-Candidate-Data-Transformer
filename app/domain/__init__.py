"""Domain models and enums (claims, canonical profile, config shapes)."""

from app.domain.enums import ExtractionMethod, OnMissing, SourceType
from app.domain.models import (
    CanonicalProfile,
    Claim,
    ClaimValue,
    Config,
    EducationEntry,
    ExperienceEntry,
    FieldSpec,
    Links,
    Location,
    Provenance,
    Skill,
)

__all__ = [
    "CanonicalProfile",
    "Claim",
    "ClaimValue",
    "Config",
    "EducationEntry",
    "ExperienceEntry",
    "ExtractionMethod",
    "FieldSpec",
    "Links",
    "Location",
    "OnMissing",
    "Provenance",
    "Skill",
    "SourceType",
]
