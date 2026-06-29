"""Normalizer registry and field normalizers (dates, phones, skills, ...)."""

from app.normalize.base import NormalizationResult, Normalizer
from app.normalize.registry import REGISTRY, normalize

__all__ = [
    "REGISTRY",
    "NormalizationResult",
    "Normalizer",
    "normalize",
]
