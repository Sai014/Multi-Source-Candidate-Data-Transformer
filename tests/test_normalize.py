"""Tests for the normalizer registry.

Covers valid, invalid, and partial cases per normalizer, idempotency
(``f(f(x)) == f(x)``), and that no normalizer raises on garbage input.
"""

from __future__ import annotations

import pytest

from app.normalize.base import NormalizationResult
from app.normalize.registry import REGISTRY, normalize

# --------------------------------------------------------------------------- #
# Valid cases                                                                 #
# --------------------------------------------------------------------------- #

VALID_CASES: list[tuple[str, str, object, str | None]] = [
    ("text_unicode", "  he\u200bllo   world\t", "hello world", None),
    ("text_unicode", "Cafe\u0301", "Caf\u00e9", None),  # NFC composition
    ("name", '  "Priya   Sharma" ', "Priya Sharma", None),
    ("email", "  Priya.Sharma@Example.COM ", "priya.sharma@example.com", None),
    ("email", "mailto:a@b.io", "a@b.io", None),
    ("phone_e164", "(415) 555-0182", "+14155550182", None),
    ("phone_e164", "+1 415 555 0182", "+14155550182", None),
    ("date_ym", "2021-03", "2021-03", None),
    ("date_ym", "03/2021", "2021-03", None),
    ("date_ym", "Mar 2021", "2021-03", None),
    ("date_ym", "March 2021", "2021-03", None),
    ("country_iso2", "United States", "US", None),
    ("country_iso2", "U.S.A.", "US", None),
    ("country_iso2", "india", "IN", None),
    ("url_link", "linkedin.com/in/x/", "https://linkedin.com/in/x", "linkedin"),
    ("url_link", "http://github.com/foo?utm_source=z", "https://github.com/foo", "github"),
    ("skill_canonical", "  js ", "JavaScript", None),
    ("skill_canonical", "Apache Spark", "Apache Spark", None),
    ("years_experience", "7+ years", 7.0, None),
    ("years_experience", "7.5", 7.5, None),
]


@pytest.mark.parametrize(("norm", "raw", "expected_value", "expected_note"), VALID_CASES)
def test_valid_cases(
    norm: str, raw: str, expected_value: object, expected_note: str | None
) -> None:
    """Valid inputs produce the expected normalized value and note."""
    result = normalize(norm, raw)
    assert result.ok is True
    assert result.value == expected_value
    assert result.note == expected_note


# --------------------------------------------------------------------------- #
# Invalid cases (honestly-empty, never crash)                                 #
# --------------------------------------------------------------------------- #

INVALID_CASES: list[tuple[str, str]] = [
    ("text_unicode", "   "),
    ("name", "\u200b"),
    ("email", "not-an-email"),
    ("email", "a@@b..com"),
    ("phone_e164", "+++bad+++"),
    ("phone_e164", "12"),
    ("date_ym", "not a date"),
    ("date_ym", "2021-13"),
    ("country_iso2", "Atlantis"),
    ("url_link", "not a url"),
    ("years_experience", "no number here"),
    ("years_experience", "999"),
]


@pytest.mark.parametrize(("norm", "raw"), INVALID_CASES)
def test_invalid_cases(norm: str, raw: str) -> None:
    """Invalid inputs are honestly-empty: ok=False, value=None, with a reason note."""
    result = normalize(norm, raw)
    assert result.ok is False
    assert result.value is None
    assert result.note is not None


# --------------------------------------------------------------------------- #
# Partial cases                                                               #
# --------------------------------------------------------------------------- #


def test_date_year_only_is_kept_with_note() -> None:
    """Year-only dates are kept verbatim with a note rather than discarded."""
    result = normalize("date_ym", "2018")
    assert result.ok is True
    assert result.value == "2018"
    assert result.note == "year_only"


def test_date_present_is_ok_but_valueless() -> None:
    """'Present'/'Current' is a valid open end with no concrete value."""
    for token in ("Present", "current", "now"):
        result = normalize("date_ym", token)
        assert result.ok is True
        assert result.value is None
        assert result.note == "present"


def test_skill_unmapped_is_non_canonical() -> None:
    """An unmapped skill is kept verbatim and flagged non_canonical."""
    result = normalize("skill_canonical", "Rust")
    assert result.ok is True
    assert result.value == "Rust"
    assert result.note == "non_canonical"


# --------------------------------------------------------------------------- #
# Idempotency: f(f(x)) == f(x)                                                #
# --------------------------------------------------------------------------- #

IDEMPOTENCY_INPUTS: list[tuple[str, str]] = [
    *[(norm, raw) for norm, raw, _, _ in VALID_CASES],
    ("date_ym", "2018"),
    ("skill_canonical", "Rust"),
    ("skill_canonical", "Google Cloud"),
    ("text_unicode", "a   b   c"),
]


@pytest.mark.parametrize(("norm", "raw"), IDEMPOTENCY_INPUTS)
def test_idempotency(norm: str, raw: str) -> None:
    """Re-normalizing a normalizer's own output reproduces value, ok, and note."""
    first = normalize(norm, raw)
    if not first.ok or first.value is None:
        pytest.skip("no stringifiable value to re-feed")
    second = normalize(norm, str(first.value))
    assert second.value == first.value
    assert second.ok == first.ok
    assert second.note == first.note


# --------------------------------------------------------------------------- #
# Robustness: no normalizer raises on garbage                                 #
# --------------------------------------------------------------------------- #

GARBAGE_INPUTS: list[str] = [
    "",
    "   ",
    "\x00\x01\x02",
    "\u200b\u200c",
    "🙂🚀💥",
    "a" * 5000,
    "<script>alert(1)</script>",
    ";;;,,,...",
    "+++",
    "\n\t\r",
]


@pytest.mark.parametrize("norm", sorted(REGISTRY))
@pytest.mark.parametrize("raw", GARBAGE_INPUTS)
def test_no_normalizer_raises_on_garbage(norm: str, raw: str) -> None:
    """Every normalizer returns a result on garbage instead of raising."""
    result = normalize(norm, raw)
    assert isinstance(result, NormalizationResult)


def test_unknown_normalizer_is_graceful() -> None:
    """Dispatching an unknown normalizer name degrades gracefully."""
    result = normalize("does_not_exist", "anything")
    assert result.ok is False
    assert result.value is None
    assert result.note == "unknown_normalizer:does_not_exist"


def test_dispatch_matches_direct_call() -> None:
    """normalize(name, raw) equals calling the registered normalizer directly."""
    direct = REGISTRY["email"]("a@b.io")
    dispatched = normalize("email", "a@b.io")
    assert dispatched == direct
