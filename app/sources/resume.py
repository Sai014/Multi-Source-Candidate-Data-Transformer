"""Resume adapter with two complementary extraction strategies.

Text is read from PDF (pdfplumber), DOCX (python-docx), or plain TXT. Two
strategies sit behind the :class:`ResumeExtractor` protocol so a future LLM
strategy can be registered without touching callers:

- :class:`SectionExtractor` (``STRUCTURED_PARSE``): heading detection plus spaCy
  NER for the candidate name; higher trust (the "resume_section" prior).
- :class:`ProseExtractor` (``REGEX_PROSE``): regex over the whole document for
  emails, phones, and a years-of-experience phrase; lower trust ("resume_prose").

Both run and all claims are appended - overlap is intentional, fusion reconciles
it. Odd formatting degrades to fewer claims; it never raises. (Both strategies use
``SourceType.RESUME``; the section/prose trust split is derived later from the
source+method pair.)
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from functools import lru_cache
from pathlib import Path
from typing import ClassVar, Protocol, cast

from app.domain.enums import ExtractionMethod, SourceType
from app.domain.models import Claim, ClaimValue, EducationEntry, ExperienceEntry, Links
from app.sources.base import register_adapter

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?\(?\d[\d\s().\-]{7,}\d")
_YEARS_RE = re.compile(r"\d+(?:\.\d+)?\+?\s*years?(?:\s+of\s+experience)?", re.IGNORECASE)
_LINKEDIN_RE = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/\S+", re.IGNORECASE)
_GITHUB_RE = re.compile(r"(?:https?://)?(?:www\.)?github\.com/\S+", re.IGNORECASE)
_EXP_HEADER_RE = re.compile(
    r"^(?P<title>[^,]+),\s*(?P<company>.+?)\s*[\u2014\u2013-]\s*(?P<start>.+?)\s+to\s+(?P<end>.+?)$"
)
_EDU_RE = re.compile(
    r"^(?P<degree>.+?)\s+in\s+(?P<field>.+?),\s*(?P<institution>.+?),\s*(?P<year>\d{4})$"
)
_MIN_PHONE_DIGITS = 10
_MAX_HEADING_LEN = 30
_HEADER_KEY = "_header"
_SECTION_ALIASES: dict[str, str] = {
    "summary": "summary", "objective": "summary",
    "experience": "experience", "work experience": "experience",
    "professional experience": "experience", "employment": "experience",
    "education": "education",
    "skills": "skills", "technical skills": "skills", "core skills": "skills",
    "projects": "projects", "certifications": "certifications",
}


# --------------------------------------------------------------------------- #
# spaCy access (contained behind a typed protocol so no Any leaks)            #
# --------------------------------------------------------------------------- #


class _SpacyEntity(Protocol):
    label_: str
    text: str


class _SpacyDoc(Protocol):
    @property
    def ents(self) -> Iterable[_SpacyEntity]: ...


class _SpacyModel(Protocol):
    def __call__(self, text: str) -> _SpacyDoc: ...


@lru_cache(maxsize=1)
def _load_nlp() -> _SpacyModel:
    import spacy

    return cast(_SpacyModel, spacy.load("en_core_web_sm"))


def _person_name(text: str) -> str | None:
    """Return the first PERSON entity spaCy finds, or None (never raises)."""
    try:
        nlp = _load_nlp()
        doc = nlp(text)
    except Exception:
        return None
    for ent in doc.ents:
        if ent.label_ == "PERSON":
            cleaned = ent.text.strip()
            if cleaned:
                return cleaned
    return None


# --------------------------------------------------------------------------- #
# Shared text helpers                                                         #
# --------------------------------------------------------------------------- #


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _find_emails(text: str) -> list[str]:
    return [match.group(0) for match in _EMAIL_RE.finditer(text)]


def _find_phones(text: str) -> list[str]:
    out: list[str] = []
    for match in _PHONE_RE.finditer(text):
        token = match.group(0).strip()
        if sum(char.isdigit() for char in token) >= _MIN_PHONE_DIGITS:
            out.append(token)
    return out


def _find_links(text: str) -> Links | None:
    linkedin_match = _LINKEDIN_RE.search(text)
    github_match = _GITHUB_RE.search(text)
    linkedin = linkedin_match.group(0).rstrip("|,. ") if linkedin_match else None
    github = github_match.group(0).rstrip("|,. ") if github_match else None
    if linkedin is None and github is None:
        return None
    return Links(linkedin=linkedin, github=github)


def _heading_key(line: str) -> str | None:
    if not line or len(line) > _MAX_HEADING_LEN:
        return None
    return _SECTION_ALIASES.get(line.rstrip(":").strip().lower())


def _split_sections(lines: Sequence[str]) -> dict[str, list[str]]:
    """Partition lines into a header block plus named sections by heading lines."""
    sections: dict[str, list[str]] = {_HEADER_KEY: []}
    current = _HEADER_KEY
    for raw in lines:
        key = _heading_key(raw.strip())
        if key is not None:
            current = key
            sections.setdefault(current, [])
            continue
        sections[current].append(raw)
    return sections


def _parse_skills(lines: Sequence[str]) -> list[str]:
    skills: list[str] = []
    for line in lines:
        for part in re.split(r"[,;|]", line):
            cleaned = part.strip()
            if cleaned:
                skills.append(cleaned)
    return _dedupe(skills)


def _parse_experience(lines: Sequence[str]) -> list[tuple[ExperienceEntry, str]]:
    """Parse 'Title, Company - Start to End' headers with following bullet summaries."""
    entries: list[tuple[ExperienceEntry, str]] = []
    header: dict[str, str] | None = None
    summary_lines: list[str] = []
    raw_lines: list[str] = []

    def flush() -> None:
        nonlocal header, summary_lines, raw_lines
        if header is not None:
            entry = ExperienceEntry(
                company=header["company"],
                title=header["title"],
                start=header["start"],
                end=header["end"],
                summary=" ".join(summary_lines) or None,
            )
            entries.append((entry, "\n".join(raw_lines)))
        header = None
        summary_lines = []
        raw_lines = []

    for raw in lines:
        line = raw.strip()
        match = _EXP_HEADER_RE.match(line)
        if match:
            flush()
            header = {key: match.group(key).strip() for key in ("title", "company", "start", "end")}
            raw_lines = [line]
        elif header is not None and line:
            summary_lines.append(line.lstrip("-\u2022 ").strip())
            raw_lines.append(line)
    flush()
    return entries


def _parse_education(lines: Sequence[str]) -> list[tuple[EducationEntry, str]]:
    entries: list[tuple[EducationEntry, str]] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        match = _EDU_RE.match(line)
        if match:
            entry = EducationEntry(
                institution=match.group("institution").strip(),
                degree=match.group("degree").strip(),
                field=match.group("field").strip(),
                end_year=int(match.group("year")),
            )
            entries.append((entry, line))
            continue
        year_match = re.search(r"\b(\d{4})\b", line)
        if year_match:
            entry = EducationEntry(institution=line, end_year=int(year_match.group(1)))
            entries.append((entry, line))
    return entries


def _resume_claim(
    field: str, value: ClaimValue, raw: str, method: ExtractionMethod
) -> Claim:
    return Claim(field=field, value=value, source=SourceType.RESUME, method=method, raw=raw)


# --------------------------------------------------------------------------- #
# Extraction strategies                                                       #
# --------------------------------------------------------------------------- #


class ResumeExtractor(Protocol):
    """A strategy that turns resume text into claims."""

    def extract(self, text: str) -> list[Claim]:
        """Return the claims this strategy can derive from ``text``."""
        ...


class SectionExtractor:
    """Heading- and entity-based extraction (higher-trust structured parse)."""

    _method = ExtractionMethod.STRUCTURED_PARSE

    def extract(self, text: str) -> list[Claim]:
        claims: list[Claim] = []
        lines = text.splitlines()
        nonempty = [line.strip() for line in lines if line.strip()]

        full_name = _person_name("\n".join(nonempty[:3]))
        if full_name is None and nonempty:
            first = nonempty[0]
            if "@" not in first and not any(char.isdigit() for char in first):
                full_name = first
        if full_name:
            claims.append(_resume_claim("full_name", full_name, full_name, self._method))

        sections = _split_sections(lines)
        header_text = "\n".join(sections.get(_HEADER_KEY, []))
        for email in _dedupe(_find_emails(header_text)):
            claims.append(_resume_claim("emails", email, email, self._method))
        for phone in _dedupe(_find_phones(header_text)):
            claims.append(_resume_claim("phones", phone, phone, self._method))
        links = _find_links(header_text)
        if links is not None:
            raw = "; ".join(filter(None, (links.linkedin, links.github)))
            claims.append(_resume_claim("links", links, raw, self._method))

        for skill in _parse_skills(sections.get("skills", [])):
            claims.append(_resume_claim("skills", skill, skill, self._method))
        for experience_entry, raw in _parse_experience(sections.get("experience", [])):
            claims.append(_resume_claim("experience", experience_entry, raw, self._method))
        for education_entry, raw in _parse_education(sections.get("education", [])):
            claims.append(_resume_claim("education", education_entry, raw, self._method))

        return claims


class ProseExtractor:
    """Whole-document regex extraction (lower-trust prose parse)."""

    _method = ExtractionMethod.REGEX_PROSE

    def extract(self, text: str) -> list[Claim]:
        claims: list[Claim] = []
        for email in _dedupe(_find_emails(text)):
            claims.append(_resume_claim("emails", email, email, self._method))
        for phone in _dedupe(_find_phones(text)):
            claims.append(_resume_claim("phones", phone, phone, self._method))
        years_match = _YEARS_RE.search(text)
        if years_match:
            phrase = years_match.group(0).strip()
            claims.append(_resume_claim("years_experience", phrase, phrase, self._method))
        return claims


# --------------------------------------------------------------------------- #
# File reading                                                                #
# --------------------------------------------------------------------------- #


def _read_pdf(path: Path) -> str:
    import pdfplumber

    parts: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            extracted = page.extract_text()
            if isinstance(extracted, str):
                parts.append(extracted)
    return "\n".join(parts)


def _read_docx(path: Path) -> str:
    import docx

    document = docx.Document(str(path))
    parts: list[str] = []
    for paragraph in document.paragraphs:
        if isinstance(paragraph.text, str):
            parts.append(paragraph.text)
    return "\n".join(parts)


def _read_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _read_pdf(path)
    if suffix == ".docx":
        return _read_docx(path)
    return path.read_text(encoding="utf-8", errors="replace")


class ResumeAdapter:
    """Adapter that reads a resume file and runs every extraction strategy."""

    source_type = SourceType.RESUME
    _SUFFIXES: ClassVar[set[str]] = {".txt", ".pdf", ".docx"}

    def __init__(self, extractors: Sequence[ResumeExtractor] | None = None) -> None:
        self._extractors: tuple[ResumeExtractor, ...] = (
            (SectionExtractor(), ProseExtractor()) if extractors is None else tuple(extractors)
        )

    def can_handle(self, path: Path) -> bool:
        """Handle resume document extensions."""
        return path.suffix.lower() in self._SUFFIXES

    def extract(self, path: Path) -> list[Claim]:
        """Read the resume and collect claims from every strategy.

        Reading failures propagate (so a garbage file is quarantined); a strategy
        that trips on odd formatting is isolated and degrades to fewer claims.
        """
        text = _read_text(path)
        claims: list[Claim] = []
        for extractor in self._extractors:
            try:
                claims.extend(extractor.extract(text))
            except Exception:
                continue
        return claims


register_adapter(ResumeAdapter())
