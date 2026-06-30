"""GitHub profile adapter.

A GitHub source is a ``.github`` file whose content names *who* to fetch - a bare
username (``octocat``), an ``@octocat`` handle, or a profile URL
(``https://github.com/octocat``). The adapter resolves the username, calls
``GET https://api.github.com/users/{username}``, and maps the public profile into
typed ``DIRECT_MAP`` claims from :data:`SourceType.GITHUB`.

The HTTP call is the only impure part and is injected (:class:`GitHubFetcher`) so
the pure pipeline stays deterministic and testable; the default fetcher uses the
standard library (no new dependency) and sets the User-Agent GitHub requires. Any
failure - an empty/invalid handle, a 404, a rate-limit, or a non-object response -
raises, so the detection framework quarantines the source instead of crashing the
run. Missing/blank profile fields simply produce no claim (honestly-empty).
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import ClassVar, Protocol
from urllib.parse import quote

from app.domain.enums import ExtractionMethod, SourceType
from app.domain.models import Claim, ClaimValue, ExperienceEntry, Links, Location
from app.sources.base import register_adapter

_API_URL = "https://api.github.com/users/{username}"
_USER_AGENT = "candidate-transformer/0.1 (+https://github.com)"
_ACCEPT = "application/vnd.github+json"
_TIMEOUT_SECONDS = 10.0
# GitHub usernames: alphanumeric or single hyphens, 1-39 chars (validated loosely
# here - the API is the real authority and rejects anything truly invalid).
_USERNAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-")
_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]+")


class GitHubFetcher(Protocol):
    """Fetches the raw ``/users/{username}`` JSON object for a username."""

    def __call__(self, username: str) -> dict[str, object]:
        """Return the parsed JSON object, or raise on any failure."""
        ...


def _http_fetch(username: str) -> dict[str, object]:
    """Default fetcher: call the public GitHub users API via the standard library."""
    request = urllib.request.Request(
        _API_URL.format(username=quote(username, safe="")),
        headers={"User-Agent": _USER_AGENT, "Accept": _ACCEPT},
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        payload = response.read().decode("utf-8")
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError("GitHub API response was not a JSON object")
    return {str(key): value for key, value in parsed.items()}


def handle_to_stem(handle: str, fallback: str = "github_user") -> str:
    """A filesystem-safe filename stem for a staged ``.github`` source.

    A GitHub source may be given as a full URL (``https://github.com/octocat``), so
    its raw text cannot be used as a filename directly. This slugifies it (keeping
    only ``[A-Za-z0-9_-]``) so callers can name the staged ``.github`` file safely.
    """
    slug = _SLUG_RE.sub("_", handle.strip()).strip("_")
    return slug[:60] or fallback


def _resolve_username(raw: str) -> str:
    """Extract a bare GitHub username from a handle, profile URL, or plain name."""
    text = raw.strip().splitlines()[0].strip() if raw.strip() else ""
    if not text:
        raise ValueError("GitHub source is empty (expected a username)")
    if "github.com" in text.lower():
        tail = text.split("github.com", 1)[1].lstrip("/")
        text = tail.split("/", 1)[0].split("?", 1)[0]
    text = text.lstrip("@").strip()
    if not text or any(char not in _USERNAME_CHARS for char in text):
        raise ValueError(f"invalid GitHub username: {raw.strip()!r}")
    return text


def _as_str(value: object) -> str | None:
    """Return a non-empty trimmed string, or None for anything else/blank."""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _raw_json(value: object) -> str:
    """Serialize a value deterministically for the provenance ``raw`` field."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _github_claim(field: str, value: ClaimValue, raw: str) -> Claim:
    return Claim(
        field=field,
        value=value,
        source=SourceType.GITHUB,
        method=ExtractionMethod.DIRECT_MAP,
        raw=raw,
    )


def _build_location(value: object) -> Location | None:
    """Best-effort parse of GitHub's free-form location (``City, Region, Country``)."""
    text = _as_str(value)
    if text is None:
        return None
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if not parts:
        return None
    city = parts[0]
    region = parts[1] if len(parts) > 1 else None
    country = parts[2] if len(parts) > 2 else None
    return Location(city=city, region=region, country=country)


def _build_links(data: dict[str, object]) -> Links | None:
    """Assemble profile/blog/twitter links from the user payload."""
    github = _as_str(data.get("html_url"))
    portfolio = _as_str(data.get("blog"))
    other: list[str] = []
    twitter = _as_str(data.get("twitter_username"))
    if twitter is not None:
        other.append(f"https://twitter.com/{twitter.lstrip('@')}")
    if github is None and portfolio is None and not other:
        return None
    return Links(github=github, portfolio=portfolio, other=other)


def _build_company(value: object) -> ExperienceEntry | None:
    """Treat the profile ``company`` as a (current) experience entry."""
    company = _as_str(value)
    if company is None:
        return None
    return ExperienceEntry(company=company.lstrip("@").strip() or company)


def _profile_claims(data: dict[str, object]) -> list[Claim]:
    """Map a GitHub user object into the canonical claim set."""
    claims: list[Claim] = []

    full_name = _as_str(data.get("name"))
    if full_name is not None:
        claims.append(_github_claim("full_name", full_name, full_name))

    email = _as_str(data.get("email"))
    if email is not None:
        claims.append(_github_claim("emails", email, email))

    bio = _as_str(data.get("bio"))
    if bio is not None:
        claims.append(_github_claim("headline", bio, bio))

    location = _build_location(data.get("location"))
    if location is not None:
        claims.append(_github_claim("location", location, _raw_json(data.get("location"))))

    links = _build_links(data)
    if links is not None:
        raw = _raw_json(
            {key: data.get(key) for key in ("html_url", "blog", "twitter_username")}
        )
        claims.append(_github_claim("links", links, raw))

    company = _build_company(data.get("company"))
    if company is not None:
        claims.append(_github_claim("experience", company, _raw_json(data.get("company"))))

    return claims


class GitHubAdapter:
    """Adapter that resolves a ``.github`` username file via the GitHub users API."""

    source_type = SourceType.GITHUB
    _SUFFIXES: ClassVar[set[str]] = {".github"}

    def __init__(self, fetcher: GitHubFetcher | None = None) -> None:
        self._fetch: GitHubFetcher = _http_fetch if fetcher is None else fetcher

    def can_handle(self, path: Path) -> bool:
        """Handle ``.github`` source files (content is a username/handle/URL)."""
        return path.suffix.lower() in self._SUFFIXES

    def extract(self, path: Path) -> list[Claim]:
        """Resolve the username, fetch the profile, and map it to typed claims.

        A bad handle or any fetch failure raises so the source is quarantined; a
        sparse profile simply yields fewer claims.
        """
        username = _resolve_username(path.read_text(encoding="utf-8"))
        data = self._fetch(username)
        return _profile_claims(data)


register_adapter(GitHubAdapter())
