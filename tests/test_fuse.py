"""Tests for fuse/adjudication on constructed clusters (the DoD scenarios)."""

from __future__ import annotations

from app.domain.enums import ExtractionMethod, SourceType
from app.domain.models import Claim, Skill
from app.pipeline.fuse import fuse


def _claim(field: str, value: str, source: SourceType, method: ExtractionMethod) -> Claim:
    return Claim(field=field, value=value, source=source, method=method, raw=value)


def _ats(field: str, value: str) -> Claim:
    return _claim(field, value, SourceType.ATS, ExtractionMethod.DIRECT_MAP)


def _resume_section(field: str, value: str) -> Claim:
    return _claim(field, value, SourceType.RESUME, ExtractionMethod.STRUCTURED_PARSE)


def _resume_prose(field: str, value: str) -> Claim:
    return _claim(field, value, SourceType.RESUME, ExtractionMethod.REGEX_PROSE)


def _cluster() -> list[Claim]:
    return [
        _ats("full_name", "Robert Smith"),
        _resume_section("full_name", "Bob Smith"),
        _ats("phones", "+1 (415) 555-0182"),
        _resume_prose("phones", "(415) 555-0182"),
        _resume_prose("years_experience", "7+ years"),
        _ats("skills", "JS"),
    ]


def test_high_trust_name_beats_low_trust_and_records_loser() -> None:
    """ATS 'Robert Smith' beats resume 'Bob Smith'; Bob is retained in provenance."""
    profile = fuse(_cluster())
    assert profile.full_name == "Robert Smith"
    superseded = [
        p for p in profile.provenance if p.field == "full_name" and p.note == "superseded"
    ]
    assert len(superseded) == 1
    assert superseded[0].source is SourceType.RESUME


def test_agreeing_phone_dedupes_and_boosts() -> None:
    """The phone agreed across ATS + prose dedupes to one value (noisy-OR boost)."""
    profile = fuse(_cluster())
    assert profile.phones == ["+14155550182"]
    # Both contributing methods are recorded for the single, deduped phone.
    phone_methods = {p.method for p in profile.provenance if p.field == "phones"}
    assert phone_methods == {ExtractionMethod.DIRECT_MAP, ExtractionMethod.REGEX_PROSE}


def test_low_confidence_years_is_withheld() -> None:
    """A lone 0.33 prose years claim is below the honesty gate -> null, retained loser."""
    profile = fuse(_cluster())
    assert profile.years_experience is None
    withheld = [
        p
        for p in profile.provenance
        if p.field == "years_experience" and p.note == "withheld_low_confidence"
    ]
    assert len(withheld) == 1


def test_skill_is_canonicalized() -> None:
    """'JS' is canonicalized to 'JavaScript' with provenance sources."""
    profile = fuse(_cluster())
    names = [skill.name for skill in profile.skills]
    assert names == ["JavaScript"]
    js = profile.skills[0]
    assert isinstance(js, Skill)
    assert js.sources == ["ats"]
    assert 0.0 < js.confidence <= 0.99


def test_fuse_is_deterministic() -> None:
    """Identical clusters produce identical profiles (including candidate_id)."""
    assert fuse(_cluster()).model_dump() == fuse(_cluster()).model_dump()


def test_candidate_id_prefers_email_key() -> None:
    """candidate_id is the sha1 of the strongest key (email when present)."""
    import hashlib

    cluster = [*_cluster(), _ats("emails", "robert@example.com")]
    profile = fuse(cluster)
    expected = hashlib.sha1(b"robert@example.com").hexdigest()
    assert profile.candidate_id == expected


def test_high_trust_disagreement_applies_penalty() -> None:
    """Two high-trust sources disagreeing penalizes the winner's confidence."""
    agree = [_ats("full_name", "Robert Smith")]
    disagree = [
        _ats("full_name", "Robert Smith"),
        _claim("full_name", "Bobby Smith", SourceType.ATS, ExtractionMethod.DIRECT_MAP),
    ]
    # The disagreeing cluster's name confidence should be lower than the clean one.
    clean = fuse(agree).overall_confidence
    penalized = fuse(disagree).overall_confidence
    assert penalized < clean
