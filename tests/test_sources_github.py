"""Tests for the GitHub profile adapter.

The HTTP fetcher is injected with a stub so these tests are deterministic and never
touch the network. They cover handle resolution (bare/``@``/URL), the claim mapping,
honestly-empty behaviour on sparse profiles, and quarantine on fetch failure.
"""

from __future__ import annotations

from pathlib import Path

from app.domain.enums import ExtractionMethod, SourceType
from app.domain.models import Claim, ExperienceEntry, Links, Location
from app.sources.detect import ingest_paths
from app.sources.github import GitHubAdapter, _resolve_username, handle_to_stem

SAMPLES = Path(__file__).resolve().parents[1] / "samples"

OCTOCAT: dict[str, object] = {
    "login": "octocat",
    "name": "The Octocat",
    "email": "octocat@github.com",
    "bio": "Open source mascot",
    "company": "@github",
    "location": "San Francisco, CA, USA",
    "blog": "https://github.blog",
    "twitter_username": "github",
    "html_url": "https://github.com/octocat",
}


def _by_field(claims: list[Claim], field: str) -> list[Claim]:
    return [claim for claim in claims if claim.field == field]


def _fetcher(payload: dict[str, object]):
    def fetch(username: str) -> dict[str, object]:
        assert username == "octocat"
        return payload

    return fetch


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "candidate.github"
    path.write_text(content, encoding="utf-8")
    return path


def test_resolve_username_accepts_url_handle_and_at() -> None:
    """A profile URL, a bare name, and an @handle all resolve to the username."""
    assert _resolve_username("https://github.com/octocat") == "octocat"
    assert _resolve_username("github.com/octocat/") == "octocat"
    assert _resolve_username("https://www.github.com/octocat?tab=repositories") == "octocat"
    assert _resolve_username("octocat") == "octocat"
    assert _resolve_username("@octocat") == "octocat"


def test_handle_to_stem_is_filesystem_safe() -> None:
    """A profile URL slugifies to a safe stem with no path separators."""
    stem = handle_to_stem("https://github.com/octocat")
    assert "/" not in stem and ":" not in stem
    assert stem == "https_github_com_octocat"
    assert handle_to_stem("   ", fallback="github_0") == "github_0"


def test_profile_url_maps_to_typed_claims(tmp_path: Path) -> None:
    """A profile URL source resolves and maps to typed GITHUB direct-map claims."""
    adapter = GitHubAdapter(fetcher=_fetcher(OCTOCAT))
    claims = adapter.extract(_write(tmp_path, "https://github.com/octocat"))

    assert all(c.source is SourceType.GITHUB for c in claims)
    assert all(c.method is ExtractionMethod.DIRECT_MAP for c in claims)

    assert [c.value for c in _by_field(claims, "full_name")] == ["The Octocat"]
    assert [c.value for c in _by_field(claims, "emails")] == ["octocat@github.com"]
    assert [c.value for c in _by_field(claims, "headline")] == ["Open source mascot"]

    location = _by_field(claims, "location")[0].value
    assert isinstance(location, Location)
    assert (location.city, location.region, location.country) == ("San Francisco", "CA", "USA")

    links = _by_field(claims, "links")[0].value
    assert isinstance(links, Links)
    assert links.github == "https://github.com/octocat"
    assert links.portfolio == "https://github.blog"
    assert links.other == ["https://twitter.com/github"]

    experience = _by_field(claims, "experience")[0].value
    assert isinstance(experience, ExperienceEntry)
    assert experience.company == "github"


def test_sparse_profile_is_honestly_empty(tmp_path: Path) -> None:
    """Null/blank profile fields produce no claim - the adapter never invents one."""
    adapter = GitHubAdapter(fetcher=_fetcher({"login": "ghost", "name": None, "email": ""}))
    claims = adapter.extract(_write(tmp_path, "octocat"))
    assert claims == []


def test_fetch_failure_is_quarantined_not_crashed(tmp_path: Path) -> None:
    """A fetch error (e.g. 404/rate-limit) is quarantined by the framework."""

    def boom(username: str) -> dict[str, object]:
        raise RuntimeError("404 Not Found")

    path = _write(tmp_path, "ghost")
    result = ingest_paths([path], adapters=[GitHubAdapter(fetcher=boom)])
    assert len(result.ledger) == 0
    assert len(result.quarantined) == 1
    assert result.quarantined[0].source is SourceType.GITHUB
    assert "404 Not Found" in result.quarantined[0].reason


def test_empty_handle_is_quarantined(tmp_path: Path) -> None:
    """A blank ``.github`` file is an invalid source and is quarantined."""
    path = _write(tmp_path, "   ")
    result = ingest_paths([path], adapters=[GitHubAdapter(fetcher=_fetcher(OCTOCAT))])
    assert len(result.quarantined) == 1
    assert result.quarantined[0].source is SourceType.GITHUB


def test_adapter_routes_dot_github_suffix() -> None:
    """The adapter claims ``.github`` files and ignores others."""
    adapter = GitHubAdapter(fetcher=_fetcher(OCTOCAT))
    assert adapter.can_handle(SAMPLES / "github_octocat.github")
    assert not adapter.can_handle(SAMPLES / "ats_sample.json")
