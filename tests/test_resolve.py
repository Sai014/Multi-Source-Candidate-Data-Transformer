"""Tests for identity resolution (clustering records into candidates)."""

from __future__ import annotations

from app.domain.enums import ExtractionMethod, SourceType
from app.domain.models import Claim
from app.pipeline.resolve import cluster


def _claim(field: str, value: str, source: SourceType, method: ExtractionMethod) -> Claim:
    return Claim(field=field, value=value, source=source, method=method, raw=value)


def _ats(field: str, value: str) -> Claim:
    return _claim(field, value, SourceType.ATS, ExtractionMethod.DIRECT_MAP)


def _resume(field: str, value: str) -> Claim:
    return _claim(field, value, SourceType.RESUME, ExtractionMethod.REGEX_PROSE)


def test_single_candidate_records_merge_on_shared_key() -> None:
    """Two records sharing an email/phone collapse into one cluster (the samples shape)."""
    ats_record = [
        _ats("full_name", "Priya Sharma"),
        _ats("emails", "priya.sharma@example.com"),
        _ats("phones", "+1 (415) 555-0182"),
    ]
    resume_record = [
        _resume("full_name", "Priya Sharma"),
        _resume("emails", "PRIYA.SHARMA@example.com"),  # differs only by case -> same key
    ]
    clusters = cluster([ats_record, resume_record])
    assert len(clusters) == 1
    assert len(clusters[0]) == 5


def test_two_candidates_do_not_merge() -> None:
    """Records with no shared key remain distinct candidates."""
    alice = [_ats("emails", "alice@example.com"), _ats("full_name", "Alice")]
    bob = [_ats("emails", "bob@example.com"), _ats("full_name", "Bob")]
    clusters = cluster([alice, bob])
    assert len(clusters) == 2
    names = sorted(
        claim.value for group in clusters for claim in group if claim.field == "full_name"
    )
    assert names == ["Alice", "Bob"]


def test_transitive_merge_via_phone_then_email() -> None:
    """A links bridge plus a phone bridge transitively unite three records."""
    record_a = [_ats("emails", "x@a.com"), _ats("phones", "+14155550182")]
    record_b = [_resume("phones", "(415) 555-0182")]  # same phone as A
    record_c = [_resume("emails", "x@a.com")]  # same email as A
    clusters = cluster([record_a, record_b, record_c])
    assert len(clusters) == 1
    assert len(clusters[0]) == 4


def test_cluster_ordering_is_stable() -> None:
    """Cluster order follows the smallest contributing record index."""
    first = [_ats("emails", "first@example.com")]
    second = [_ats("emails", "second@example.com")]
    third = [_ats("emails", "first@example.com")]  # merges with `first`
    clusters = cluster([first, second, third])
    assert len(clusters) == 2
    assert [c.value for c in clusters[0]] == ["first@example.com", "first@example.com"]
    assert [c.value for c in clusters[1]] == ["second@example.com"]


def test_empty_input_yields_no_clusters() -> None:
    """No records means no candidates."""
    assert cluster([]) == []


def test_keyless_record_stays_separate() -> None:
    """A record with no match key cannot be linked and stays on its own."""
    keyed = [_ats("emails", "k@example.com")]
    keyless = [_ats("full_name", "No Keys Here")]
    clusters = cluster([keyed, keyless])
    assert len(clusters) == 2
