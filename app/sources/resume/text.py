"""Shared resume text normalization and structured parsing helpers."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from app.domain.enums import ExtractionMethod, SourceType
from app.domain.models import Claim, ClaimValue, EducationEntry, ExperienceEntry, Links, Location

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?\(?\d[\d\s().\-]{7,}\d")
_YEARS_RE = re.compile(r"\d+(?:\.\d+)?\+?\s*years?(?:\s+of\s+experience)?", re.IGNORECASE)
_LINKEDIN_RE = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/\S+", re.IGNORECASE)
_GITHUB_RE = re.compile(r"(?:https?://)?(?:www\.)?github\.com/\S+", re.IGNORECASE)
_EXP_HEADER_RE = re.compile(
    r"^(?P<title>[^,]+),\s*(?P<company>.+?)\s*[\u2014\u2013-]\s*(?P<start>.+?)\s+to\s+(?P<end>.+?)$"
)
_EXP_TITLE_DASH_COMPANY_RE = re.compile(r"^(?P<title>.+?)\s+-\s+(?P<company>.+)$")
_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_DATE_SPAN = (
    rf"(?P<start>{_MONTH}\.?\s+\d{{4}}|\d{{4}})\s*[\u2013\u2014\u2012\u2015\-]\s*"
    rf"(?P<end>Present|Current|{_MONTH}\.?\s+\d{{4}}|\d{{4}})"
)
_EXP_TITLE_DATES_RE = re.compile(rf"^(?P<title>.+?)\s+{_DATE_SPAN}$", re.IGNORECASE)
_EXP_DATES_ONLY_RE = re.compile(rf"^{_DATE_SPAN}$", re.IGNORECASE)
_EDU_RE = re.compile(
    r"^(?P<degree>.+?)\s+in\s+(?P<field>.+?),\s*(?P<institution>.+?),\s*(?P<year>\d{4})$"
)
_EDU_DEGREE_INST_PARENS_RE = re.compile(
    r"^(?P<degree>.+?)\s+in\s+(?P<field>.+?),\s*(?P<institution>.+?)\s*\((?P<year>\d{4})\)\s*$",
    re.IGNORECASE,
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


def _repair_glued_line(line: str) -> str:
    """Insert spaces into long PDF lines where words were concatenated without spaces."""
    stripped = line.strip()
    if len(stripped) < 25:
        return line
    if stripped.count(" ") >= max(3, len(stripped) // 20):
        return line
    repaired = re.sub(r"([a-z])([A-Z])", r"\1 \2", stripped)
    repaired = re.sub(r"([a-zA-Z])(\d)", r"\1 \2", repaired)
    return repaired


def _normalize_resume_text(text: str) -> str:
    """Repair common PDF extraction artifacts before parsing."""
    repaired = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    repaired = repaired.replace("\ufffd", " \u2013 ")
    return "\n".join(_repair_glued_line(line) for line in repaired.splitlines())


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


def _strip_skill_label(line: str) -> str:
    """Drop a leading ``Category:`` label (e.g. ``Languages & Frameworks: ...``).

    Only strips when the label precedes the first comma, so a genuine skill that
    happens to contain a colon is left intact.
    """
    colon = line.find(":")
    comma = line.find(",")
    if colon != -1 and (comma == -1 or colon < comma):
        return line[colon + 1 :]
    return line


def _parse_skills(lines: Sequence[str]) -> list[str]:
    skills: list[str] = []
    for line in lines:
        for part in re.split(r"[,;|]", _strip_skill_label(line)):
            cleaned = part.strip()
            if cleaned and any(char.isalnum() for char in cleaned):
                skills.append(cleaned)
    return _dedupe(_merge_hyphenated_skills(skills))


def _merge_hyphenated_skills(skills: list[str]) -> list[str]:
    """Join skills split across PDF line breaks (e.g. ``Tem-`` + ``poral``)."""
    merged: list[str] = []
    index = 0
    while index < len(skills):
        current = skills[index]
        if current.endswith("-") and index + 1 < len(skills):
            merged.append(current[:-1] + skills[index + 1])
            index += 2
            continue
        merged.append(current)
        index += 1
    return merged


def _is_bullet_line(line: str) -> bool:
    stripped = line.lstrip()
    if not stripped:
        return False
    return stripped[0] in "-•·*\ufffd"


def _looks_like_company_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or _is_bullet_line(stripped):
        return False
    if _looks_like_institution(stripped) or _looks_like_degree(stripped):
        return False
    if _EXP_TITLE_DATES_RE.match(stripped) or _EXP_DATES_ONLY_RE.match(stripped):
        return False
    if _EXP_TITLE_DASH_COMPANY_RE.match(stripped) or _EXP_HEADER_RE.match(stripped):
        return False
    return stripped[0].isupper()


def _exp_header_from_match(match: re.Match[str]) -> dict[str, str]:
    return {key: match.group(key).strip() for key in match.groupdict()}


def _parse_experience(lines: Sequence[str]) -> list[tuple[ExperienceEntry, str]]:
    """Parse common experience layouts with following bullet summaries."""
    entries: list[tuple[ExperienceEntry, str]] = []
    header: dict[str, str] | None = None
    summary_lines: list[str] = []
    raw_lines: list[str] = []
    indexed = [raw.strip() for raw in lines]

    def flush() -> None:
        nonlocal header, summary_lines, raw_lines
        if header is not None:
            entry = ExperienceEntry(
                company=header.get("company") or None,
                title=header.get("title") or None,
                start=header.get("start") or None,
                end=header.get("end") or None,
                summary=" ".join(summary_lines) or None,
            )
            entries.append((entry, "\n".join(raw_lines)))
        header = None
        summary_lines = []
        raw_lines = []

    def start_header(parsed: dict[str, str], raw: list[str]) -> None:
        nonlocal header, raw_lines
        flush()
        header = parsed
        raw_lines = raw

    index = 0
    while index < len(indexed):
        line = indexed[index]
        if not line:
            index += 1
            continue

        if _is_bullet_line(line):
            if header is not None:
                summary_lines.append(line.lstrip("-•·* ").strip())
                raw_lines.append(line)
            index += 1
            continue

        comma_match = _EXP_HEADER_RE.match(line)
        if comma_match:
            start_header(_exp_header_from_match(comma_match), [line])
            index += 1
            continue

        dash_match = _EXP_TITLE_DASH_COMPANY_RE.match(line)
        if dash_match and not _EXP_DATES_ONLY_RE.match(line):
            parsed = _exp_header_from_match(dash_match)
            parsed.setdefault("start", "")
            parsed.setdefault("end", "")
            raw = [line]
            if index + 1 < len(indexed):
                dates_match = _EXP_DATES_ONLY_RE.match(indexed[index + 1])
                if dates_match:
                    parsed["start"] = dates_match.group("start").strip()
                    parsed["end"] = dates_match.group("end").strip()
                    raw.append(indexed[index + 1])
                    index += 1
            start_header(parsed, raw)
            index += 1
            continue

        if index + 1 < len(indexed):
            title_dates = _EXP_TITLE_DATES_RE.match(indexed[index + 1])
            if title_dates and _looks_like_company_line(line):
                parsed = {
                    "company": line,
                    "title": title_dates.group("title").strip(),
                    "start": title_dates.group("start").strip(),
                    "end": title_dates.group("end").strip(),
                }
                start_header(parsed, [line, indexed[index + 1]])
                index += 2
                continue

        title_dates = _EXP_TITLE_DATES_RE.match(line)
        if title_dates:
            parsed = {
                "company": header.get("company", "") if header else "",
                "title": title_dates.group("title").strip(),
                "start": title_dates.group("start").strip(),
                "end": title_dates.group("end").strip(),
            }
            if header is None:
                start_header(parsed, [line])
            else:
                header.update({k: v for k, v in parsed.items() if v})
                raw_lines.append(line)
            index += 1
            continue

        if header is not None:
            summary_lines.append(line)
            raw_lines.append(line)
        index += 1

    flush()
    return entries


_DEGREE_RE = re.compile(
    r"\b(bachelor|master|associate|doctor|b\.?s\.?|m\.?s\.?|b\.?a\.?|m\.?a\.?|"
    r"ph\.?\s*d|mba|b\.?tech|m\.?tech|diploma)\b",
    re.IGNORECASE,
)
_INSTITUTION_RE = re.compile(
    r"\b(university|college|institute|institution|school|academy|polytechnic)\b",
    re.IGNORECASE,
)
_INSTITUTION_HINTS = (
    "institute of technology",
    "high school",
    "university",
    "college",
    "polytechnic",
    "academy",
)
_SECONDARY_EDU_RE = re.compile(r"\bsecondary education\b|\(\s*xii\s*\)|\(\s*x\s*\)", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_GRAD_NOISE_RE = re.compile(r"\b(cgpa|gpa)\s*:.*$|\b\d+\.\d+%.*$", re.IGNORECASE)


def _looks_like_institution(text: str) -> bool:
    if _INSTITUTION_RE.search(text):
        return True
    lowered = text.lower()
    return any(hint in lowered for hint in _INSTITUTION_HINTS)


def _looks_like_degree(text: str) -> bool:
    return _DEGREE_RE.search(text) is not None or _SECONDARY_EDU_RE.search(text) is not None


def _education_year(line: str) -> int | None:
    years = [int(match.group(0)) for match in _YEAR_RE.finditer(line)]
    if not years:
        return None
    return years[-1]


def _clean_education_line(text: str) -> str:
    cleaned = text.replace("\ufffd", " \u2013 ")
    cleaned = _GRAD_NOISE_RE.sub("", cleaned)
    cleaned = re.sub(r"[\s\u2013\u2014\u2012\u2015\-]+$", "", cleaned)
    return cleaned.strip(" ,-")


def _split_degree_field(text: str) -> tuple[str, str | None]:
    """Split ``Bachelor of Science in Software Engineering`` into degree + field."""
    cleaned = _clean_education_line(text)
    parts = re.split(r"\s+in\s+", cleaned, maxsplit=1, flags=re.IGNORECASE)
    degree = parts[0].strip(" ,")
    field = parts[1].strip(" ,") if len(parts) == 2 else None
    if field is not None:
        field = re.sub(r"\s*\(\s*CGPA.*$", "", field, flags=re.IGNORECASE).strip(" ,(")
    return degree, (field or None)


def _parse_education(lines: Sequence[str]) -> list[tuple[EducationEntry, str]]:
    """Pair institution and degree lines into education entries."""
    entries: list[tuple[EducationEntry, str]] = []
    institution: str | None = None
    raws: list[str] = []

    def flush() -> None:
        nonlocal institution, raws
        if institution is not None:
            entries.append((
                EducationEntry(institution=institution, degree=None, field=None, end_year=None),
                "\n".join(raws),
            ))
        institution, raws = None, []

    def append_entry(entry: EducationEntry, raw: str) -> None:
        nonlocal institution, raws
        institution = None
        raws = []
        entries.append((entry, raw))

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        single = _EDU_RE.match(line)
        if single:
            flush()
            append_entry(
                EducationEntry(
                    institution=single.group("institution").strip(),
                    degree=single.group("degree").strip(),
                    field=single.group("field").strip(),
                    end_year=int(single.group("year")),
                ),
                line,
            )
            continue

        parens = _EDU_DEGREE_INST_PARENS_RE.match(line)
        if parens:
            flush()
            append_entry(
                EducationEntry(
                    institution=parens.group("institution").strip(),
                    degree=parens.group("degree").strip(),
                    field=parens.group("field").strip(),
                    end_year=int(parens.group("year")),
                ),
                line,
            )
            continue

        if _looks_like_institution(line):
            flush()
            institution = _clean_education_line(line)
            raws = [line]
            continue

        if _looks_like_degree(line):
            degree, field = _split_degree_field(line)
            end_year = _education_year(line)
            if institution is not None:
                append_entry(
                    EducationEntry(
                        institution=institution,
                        degree=degree,
                        field=field,
                        end_year=end_year,
                    ),
                    "\n".join([*raws, line]),
                )
            else:
                append_entry(
                    EducationEntry(
                        institution=None,
                        degree=degree,
                        field=field,
                        end_year=end_year,
                    ),
                    line,
                )
            continue

        if institution is not None and _education_year(line) is not None:
            raws.append(line)

    flush()
    return entries


def _resume_claim(
    field: str, value: ClaimValue, raw: str, method: ExtractionMethod
) -> Claim:
    return Claim(field=field, value=value, source=SourceType.RESUME, method=method, raw=raw)


def _header_full_name(nonempty: Sequence[str]) -> str | None:
    """Best-effort name from the header block without NER (first plausible line)."""
    if not nonempty:
        return None
    first = nonempty[0]
    if "@" not in first and not any(char.isdigit() for char in first):
        return first
    return None


def _location_from_text(text: str) -> Location:
    """Parse a free-form location string into a :class:`Location`."""
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) >= 3:
        return Location(city=parts[0], region=parts[1], country=parts[2])
    if len(parts) == 2:
        return Location(city=parts[0], region=parts[1])
    return Location(city=text.strip() or None)

