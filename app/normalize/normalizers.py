"""Concrete, pure, total, idempotent normalizers.

Each function takes a raw string and returns a :class:`NormalizationResult`.
Unparseable or unknown input yields ``ok=False`` with ``value=None`` - the
system stays honestly-empty and never invents a value.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import phonenumbers

from app.normalize.base import NormalizationResult

_WHITESPACE_RE = re.compile(r"\s+")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_MAX_YEARS = 80.0


def _clean_text(raw: str) -> str:
    """Return NFC text with control/format chars dropped and whitespace collapsed.

    Idempotent: the output contains no control chars and only single spaces, so a
    second pass is a no-op.
    """
    text = unicodedata.normalize("NFC", raw)
    kept: list[str] = []
    for char in text:
        if char.isspace():
            kept.append(" ")
        elif unicodedata.category(char).startswith("C"):
            continue
        else:
            kept.append(char)
    return _WHITESPACE_RE.sub(" ", "".join(kept)).strip()


def text_unicode(raw: str) -> NormalizationResult:
    """Unicode-normalize and tidy free text. Used as a pre-pass for other fields."""
    cleaned = _clean_text(raw)
    if not cleaned:
        return NormalizationResult(value=None, ok=False, note="empty")
    return NormalizationResult(value=cleaned, ok=True, note=None)


def name(raw: str) -> NormalizationResult:
    """Normalize a person's name (clean text; strip wrapping quotes)."""
    cleaned = _clean_text(raw).strip("\"'`")
    cleaned = _clean_text(cleaned)
    if not cleaned:
        return NormalizationResult(value=None, ok=False, note="empty")
    return NormalizationResult(value=cleaned, ok=True, note=None)


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def email(raw: str) -> NormalizationResult:
    """Lowercase and validate an email address."""
    cleaned = _clean_text(raw).lower().removeprefix("mailto:").strip("<>")
    if _EMAIL_RE.match(cleaned):
        return NormalizationResult(value=cleaned, ok=True, note=None)
    return NormalizationResult(value=None, ok=False, note="invalid_email")


def phone_e164(raw: str, region: str = "US") -> NormalizationResult:
    """Format a phone number as E.164. ``region`` is the default parsing region."""
    cleaned = _clean_text(raw)
    if not cleaned:
        return NormalizationResult(value=None, ok=False, note="empty")
    try:
        parsed = phonenumbers.parse(cleaned, region)
    except phonenumbers.NumberParseException:
        return NormalizationResult(value=None, ok=False, note="unparseable_phone")
    if not phonenumbers.is_valid_number(parsed):
        return NormalizationResult(value=None, ok=False, note="invalid_phone")
    e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    return NormalizationResult(value=e164, ok=True, note=None)


_MONTHS: dict[str, int] = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_PRESENT_TOKENS = {"present", "current", "now", "ongoing", "till date", "to date"}
_YEAR_MONTH_RE = re.compile(r"^(\d{4})[-/.](\d{1,2})$")
_MONTH_YEAR_RE = re.compile(r"^(\d{1,2})[-/.](\d{4})$")
_WORD_YEAR_RE = re.compile(r"^([a-z]+)\.?\s+(\d{4})$")
_YEAR_ONLY_RE = re.compile(r"^(\d{4})$")
_MIN_YEAR = 1900
_MAX_YEAR = 2100


def _valid_year(year: int) -> bool:
    return _MIN_YEAR <= year <= _MAX_YEAR


def date_ym(raw: str) -> NormalizationResult:
    """Normalize a date to ``YYYY-MM``.

    Year-only input is kept with a note; "Present"/"Current" maps to
    ``(None, True, "present")``; anything unparseable is ``(None, False, ...)``.
    """
    cleaned = _clean_text(raw)
    if not cleaned:
        return NormalizationResult(value=None, ok=False, note="empty")
    lowered = cleaned.lower()
    if lowered in _PRESENT_TOKENS:
        return NormalizationResult(value=None, ok=True, note="present")

    ym = _YEAR_MONTH_RE.match(lowered)
    if ym:
        year, month = int(ym.group(1)), int(ym.group(2))
        if _valid_year(year) and 1 <= month <= 12:
            return NormalizationResult(value=f"{year:04d}-{month:02d}", ok=True, note=None)
        return NormalizationResult(value=None, ok=False, note="invalid_date")

    my = _MONTH_YEAR_RE.match(lowered)
    if my:
        month, year = int(my.group(1)), int(my.group(2))
        if _valid_year(year) and 1 <= month <= 12:
            return NormalizationResult(value=f"{year:04d}-{month:02d}", ok=True, note=None)
        return NormalizationResult(value=None, ok=False, note="invalid_date")

    wy = _WORD_YEAR_RE.match(lowered)
    if wy:
        month_num = _MONTHS.get(wy.group(1))
        year = int(wy.group(2))
        if month_num is not None and _valid_year(year):
            return NormalizationResult(value=f"{year:04d}-{month_num:02d}", ok=True, note=None)
        return NormalizationResult(value=None, ok=False, note="invalid_date")

    yo = _YEAR_ONLY_RE.match(lowered)
    if yo:
        year = int(yo.group(1))
        if _valid_year(year):
            return NormalizationResult(value=f"{year:04d}", ok=True, note="year_only")
        return NormalizationResult(value=None, ok=False, note="invalid_date")

    return NormalizationResult(value=None, ok=False, note="invalid_date")


_COUNTRY_ALIASES: dict[str, str] = {
    "us": "US", "usa": "US", "united states": "US",
    "united states of america": "US", "america": "US",
    "uk": "GB", "gb": "GB", "united kingdom": "GB",
    "great britain": "GB", "britain": "GB", "england": "GB",
    "in": "IN", "india": "IN",
    "ca": "CA", "canada": "CA",
    "de": "DE", "germany": "DE", "deutschland": "DE",
    "fr": "FR", "france": "FR",
    "au": "AU", "australia": "AU",
    "sg": "SG", "singapore": "SG",
    "ie": "IE", "ireland": "IE",
    "nl": "NL", "netherlands": "NL", "holland": "NL",
    "es": "ES", "spain": "ES",
    "it": "IT", "italy": "IT",
    "jp": "JP", "japan": "JP",
    "cn": "CN", "china": "CN",
    "br": "BR", "brazil": "BR",
    "mx": "MX", "mexico": "MX",
}


def country_iso2(raw: str) -> NormalizationResult:
    """Map a country name or code to its ISO-3166 alpha-2 code via a fixed table."""
    cleaned = _clean_text(raw)
    key = cleaned.lower().replace(".", "").strip()
    if not key:
        return NormalizationResult(value=None, ok=False, note="empty")
    iso = _COUNTRY_ALIASES.get(key)
    if iso is None:
        return NormalizationResult(value=None, ok=False, note="unknown_country")
    return NormalizationResult(value=iso, ok=True, note=None)


_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = {"gclid", "fbclid", "ref", "ref_src", "mc_cid", "mc_eid", "igshid", "_ga"}


def _is_tracking_param(key: str) -> bool:
    lowered = key.lower()
    return lowered.startswith(_TRACKING_PREFIXES) or lowered in _TRACKING_KEYS


def _classify_url(host: str) -> str:
    if "linkedin.com" in host:
        return "linkedin"
    if "github.com" in host:
        return "github"
    return "other"


def url_link(raw: str) -> NormalizationResult:
    """Canonicalize a URL: force https, drop tracking params + trailing slash, classify.

    The ``note`` carries the classification (``linkedin``/``github``/``other``).
    """
    cleaned = _clean_text(raw)
    if not cleaned:
        return NormalizationResult(value=None, ok=False, note="empty")
    candidate = cleaned if "://" in cleaned else f"https://{cleaned}"
    parts = urlsplit(candidate)
    host = parts.netloc.lower()
    if not host or "." not in host:
        return NormalizationResult(value=None, ok=False, note="invalid_url")
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not _is_tracking_param(k)]
    rebuilt = urlunsplit(("https", host, parts.path.rstrip("/"), urlencode(kept), ""))
    return NormalizationResult(value=rebuilt, ok=True, note=_classify_url(host))


_SKILL_ALIASES: dict[str, str] = {
    "python": "Python", "py": "Python",
    "javascript": "JavaScript", "js": "JavaScript",
    "typescript": "TypeScript", "ts": "TypeScript",
    "go": "Go", "golang": "Go",
    "aws": "AWS", "amazon web services": "AWS",
    "gcp": "Google Cloud", "google cloud": "Google Cloud",
    "google cloud platform": "Google Cloud",
    "sql": "SQL",
    "postgres": "PostgreSQL", "postgresql": "PostgreSQL",
    "spark": "Apache Spark", "apache spark": "Apache Spark",
    "airflow": "Apache Airflow", "apache airflow": "Apache Airflow",
    "kafka": "Apache Kafka", "apache kafka": "Apache Kafka",
    "k8s": "Kubernetes", "kubernetes": "Kubernetes",
    "docker": "Docker",
    "react": "React", "reactjs": "React", "react.js": "React",
    "node": "Node.js", "nodejs": "Node.js", "node.js": "Node.js",
    "tensorflow": "TensorFlow", "tf": "TensorFlow",
    "pytorch": "PyTorch",
}


def skill_canonical(raw: str) -> NormalizationResult:
    """Map a skill to its canonical name via a static alias table.

    Unmapped skills are kept verbatim with ``note="non_canonical"`` (never dropped,
    never invented).
    """
    cleaned = _clean_text(raw)
    if not cleaned:
        return NormalizationResult(value=None, ok=False, note="empty")
    canonical = _SKILL_ALIASES.get(cleaned.lower())
    if canonical is None:
        return NormalizationResult(value=cleaned, ok=True, note="non_canonical")
    return NormalizationResult(value=canonical, ok=True, note=None)


def years_experience(raw: str) -> NormalizationResult:
    """Parse a years-of-experience number and sanity-bound it to ``[0, 80]``."""
    cleaned = _clean_text(raw)
    match = _NUMBER_RE.search(cleaned)
    if match is None:
        return NormalizationResult(value=None, ok=False, note="unparseable")
    number = float(match.group())
    if number < 0.0 or number > _MAX_YEARS:
        return NormalizationResult(value=None, ok=False, note="out_of_range")
    return NormalizationResult(value=number, ok=True, note=None)
