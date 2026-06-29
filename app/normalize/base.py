"""Base types for normalization.

A normalizer turns one raw string into a :class:`NormalizationResult`. Every
normalizer is **pure** (no I/O, no globals, no clock), **total** (never raises,
even on garbage), and **idempotent** (``f(f(x)) == f(x)`` over its own output).
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from app.domain.models import ClaimValue


class NormalizationResult(BaseModel):
    """Outcome of normalizing a single raw value.

    ``ok`` is the honesty signal: ``False`` means "could not produce a trustworthy
    value" (the value is then ``None``), never a fabricated guess.
    """

    value: ClaimValue | None = None
    ok: bool
    note: str | None = None


class Normalizer(Protocol):
    """Structural type for a normalizer callable."""

    def __call__(self, raw: str) -> NormalizationResult:
        """Normalize ``raw`` into a :class:`NormalizationResult`."""
        ...
