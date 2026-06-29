"""Normalizer registry and dispatcher.

Implementations register themselves here by name; callers dispatch by string
(driven by config) rather than hardcoding branches.
"""

from __future__ import annotations

from app.normalize.base import NormalizationResult, Normalizer
from app.normalize.normalizers import (
    country_iso2,
    date_ym,
    email,
    name,
    phone_e164,
    skill_canonical,
    text_unicode,
    url_link,
    years_experience,
)

REGISTRY: dict[str, Normalizer] = {
    "text_unicode": text_unicode,
    "name": name,
    "email": email,
    "phone_e164": phone_e164,
    "date_ym": date_ym,
    "country_iso2": country_iso2,
    "url_link": url_link,
    "skill_canonical": skill_canonical,
    "years_experience": years_experience,
}


def normalize(name: str, raw: str) -> NormalizationResult:
    """Dispatch to the registered normalizer ``name``.

    An unknown normalizer name yields ``ok=False`` rather than raising, so a bad
    config degrades gracefully.
    """
    normalizer = REGISTRY.get(name)
    if normalizer is None:
        return NormalizationResult(value=None, ok=False, note=f"unknown_normalizer:{name}")
    return normalizer(raw)
