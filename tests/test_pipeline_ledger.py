"""Tests for the append-only claim ledger."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.enums import ExtractionMethod, SourceType
from app.domain.models import Claim
from app.pipeline.ledger import ClaimLedger


def _claim(field: str, value: str) -> Claim:
    return Claim(
        field=field,
        value=value,
        source=SourceType.ATS,
        method=ExtractionMethod.DIRECT_MAP,
        raw=value,
    )


def test_append_and_extend_preserve_order() -> None:
    """Claims read back in insertion order."""
    ledger = ClaimLedger()
    ledger.append(_claim("full_name", "A"))
    ledger.extend([_claim("emails", "b@x.io"), _claim("headline", "C")])
    assert [c.value for c in ledger.claims] == ["A", "b@x.io", "C"]
    assert len(ledger) == 3


def test_claims_view_is_immutable_snapshot() -> None:
    """The claims view is a tuple that cannot be mutated."""
    ledger = ClaimLedger()
    ledger.append(_claim("full_name", "A"))
    view = ledger.claims
    assert isinstance(view, tuple)
    with pytest.raises(TypeError):
        view[0] = _claim("full_name", "B")  # type: ignore[index]


def test_claims_view_does_not_leak_internal_list() -> None:
    """Mutating a derived list does not affect the ledger's contents."""
    ledger = ClaimLedger()
    ledger.append(_claim("full_name", "A"))
    derived = list(ledger.claims)
    derived.append(_claim("full_name", "B"))
    assert len(ledger) == 1


def test_appended_claim_is_frozen() -> None:
    """A claim already in the ledger cannot be mutated in place."""
    ledger = ClaimLedger()
    ledger.append(_claim("full_name", "A"))
    stored = ledger.claims[0]
    with pytest.raises(ValidationError):
        stored.value = "tampered"  # type: ignore[misc]


def test_ledger_exposes_no_mutators() -> None:
    """The ledger offers no way to remove or overwrite existing claims."""
    for forbidden in ("pop", "remove", "clear", "insert", "__setitem__", "__delitem__"):
        assert not hasattr(ClaimLedger, forbidden)
