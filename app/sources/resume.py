"""Resume adapter with complementary extraction strategies.

Text is read from PDF (pdfplumber), DOCX (python-docx), or plain TXT. Three
strategies sit behind the :class:`ResumeExtractor` protocol so a future LLM
strategy can be registered without touching callers:

- :class:`ProseExtractor` (``REGEX_PROSE``): regex over the whole document for
  emails, phones, URLs, and years-of-experience phrases; lower trust
  ("resume_prose").
- :class:`SectionExtractor` (``STRUCTURED_PARSE``): heading detection and
  section parsing for skills, education, and experience; higher trust
  ("resume_section").
- :class:`GlinerExtractor` (``NER``): semantic entity recognition over the full
  document (in overlapping chunks within GLiNER's token limit) for names,
  organizations, education signals, titles, certifications, and locations.

All strategies run and claims are appended — overlap is intentional, fusion
reconciles it. Odd formatting degrades to fewer claims; it never raises.
(All strategies use ``SourceType.RESUME``; the section/prose trust split is
derived later from the source+method pair.)
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from functools import lru_cache
from pathlib import Path
from typing import ClassVar, Protocol, TypedDict, cast

from app.domain.enums import ExtractionMethod, SourceType
from app.domain.models import Claim, ClaimValue, EducationEntry, ExperienceEntry, Links, Location
from app.sources.base import register_adapter

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
    rf"(?P<start>{_MONTH}\.?\s+\d{{4}}|\d{{4}})\s*[\u2013\u2014\-–—]\s*"
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
    repaired = repaired.replace("\ufffd", " – ")
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
    cleaned = text.replace("\ufffd", " – ")
    cleaned = _GRAD_NOISE_RE.sub("", cleaned)
    cleaned = re.sub(r"[\s\u2013\u2014\-–—]+$", "", cleaned)
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


# --------------------------------------------------------------------------- #
# GLiNER access (contained behind typed protocols so no Any leaks)            #
# --------------------------------------------------------------------------- #

_GLINER_MODEL_ID = "urchade/gliner_medium-v2.1"
_GLINER_THRESHOLD = 0.5
# GLiNER truncates at 384 tokens; ~3.5 chars/token is conservative for resume prose.
_GLINER_MAX_CHARS = 1200
_GLINER_OVERLAP_CHARS = 200
_GLINER_LABELS: tuple[str, ...] = (
    "person",
    "organization",
    "university",
    "degree",
    "job title",
    "certification",
    "location",
)


class _GlinerEntity(TypedDict):
    text: str
    label: str
    start: int
    end: int
    score: float


class _GlinerModel(Protocol):
    def predict_entities(
        self,
        text: str,
        labels: list[str],
        threshold: float = ...,
    ) -> list[_GlinerEntity]: ...


@lru_cache(maxsize=1)
def _load_gliner_model() -> _GlinerModel:
    from gliner import GLiNER

    return cast(_GlinerModel, GLiNER.from_pretrained(_GLINER_MODEL_ID))


def _split_gliner_chunks(text: str) -> list[tuple[str, int]]:
    """Split resume text into overlapping windows that fit GLiNER's token limit."""
    if len(text) <= _GLINER_MAX_CHARS:
        return [(text, 0)]

    chunks: list[tuple[str, int]] = []
    start = 0
    while start < len(text):
        end = min(start + _GLINER_MAX_CHARS, len(text))
        if end < len(text):
            newline = text.rfind("\n", start, end)
            if newline > start:
                end = newline + 1
        chunks.append((text[start:end], start))
        if end >= len(text):
            break
        start = max(start + 1, end - _GLINER_OVERLAP_CHARS)
    return chunks


def _entity_key(entity: _GlinerEntity) -> tuple[str, str]:
    return (entity["label"].strip().lower(), entity["text"].strip())


def _globalize_entity(entity: _GlinerEntity, chunk_start: int) -> _GlinerEntity:
    return {
        "text": entity["text"],
        "label": entity["label"],
        "start": entity["start"] + chunk_start,
        "end": entity["end"] + chunk_start,
        "score": entity["score"],
    }


def _merge_entities(existing: _GlinerEntity, incoming: _GlinerEntity) -> _GlinerEntity:
    """Keep the higher-scoring span; ties favor the earlier global offset."""
    if incoming["score"] > existing["score"]:
        return incoming
    if incoming["score"] < existing["score"]:
        return existing
    return incoming if incoming["start"] < existing["start"] else existing


def _dedupe_entities(entities: Iterable[_GlinerEntity]) -> list[_GlinerEntity]:
    merged: dict[tuple[str, str], _GlinerEntity] = {}
    for entity in entities:
        key = _entity_key(entity)
        cleaned = entity["text"].strip()
        if not cleaned:
            continue
        if key not in merged:
            merged[key] = entity
        else:
            merged[key] = _merge_entities(merged[key], entity)
    return sorted(
        merged.values(),
        key=lambda entity: (entity["start"], entity["end"], entity["label"], entity["text"]),
    )


def _predict_entities_on_chunk(model: _GlinerModel, chunk: str) -> list[_GlinerEntity]:
    return model.predict_entities(
        chunk,
        list(_GLINER_LABELS),
        threshold=_GLINER_THRESHOLD,
    )


def _predict_entities(text: str) -> list[_GlinerEntity]:
    """Run GLiNER over chunked resume text; return deduped entities in stable order."""
    model = _load_gliner_model()
    all_entities: list[_GlinerEntity] = []
    for chunk, chunk_start in _split_gliner_chunks(text):
        chunk_entities = _predict_entities_on_chunk(model, chunk)
        all_entities.extend(_globalize_entity(entity, chunk_start) for entity in chunk_entities)
    return _dedupe_entities(all_entities)


_NER_PROXIMITY = 250
_NON_EMPLOYER_ORGANIZATIONS = frozenset({
    "google cloud",
    "aws",
    "azure",
    "docker",
    "kubernetes",
    "postgresql",
    "mongodb",
    "redis",
    "fastapi",
    "python",
})


def _entity_distance(left: _GlinerEntity, right: _GlinerEntity) -> int:
    if left["end"] <= right["start"]:
        return right["start"] - left["end"]
    if right["end"] <= left["start"]:
        return left["start"] - right["end"]
    return 0


def _closest_entity(
    anchor: _GlinerEntity,
    candidates: Sequence[_GlinerEntity],
    used: set[int],
) -> tuple[_GlinerEntity | None, int | None]:
    best: _GlinerEntity | None = None
    best_index: int | None = None
    best_distance = _NER_PROXIMITY + 1
    for index, candidate in enumerate(candidates):
        if index in used:
            continue
        distance = _entity_distance(anchor, candidate)
        if distance < best_distance:
            best_distance = distance
            best = candidate
            best_index = index
    if best is None:
        return None, None
    return best, best_index


def _education_from_degree_text(text: str) -> tuple[str, str | None]:
    if _looks_like_degree(text):
        return _split_degree_field(text)
    return text.strip(), None


def _is_org_fragment(short: str, organizations: Sequence[str]) -> bool:
    short_lower = short.lower()
    for other in organizations:
        other_lower = other.lower()
        if other_lower == short_lower:
            continue
        if short_lower in other_lower and len(other_lower) - len(short_lower) > 4:
            return True
    return False


def _usable_organizations(entities: Sequence[_GlinerEntity]) -> list[_GlinerEntity]:
    names = [entity["text"].strip() for entity in entities]
    usable: list[_GlinerEntity] = []
    for entity in entities:
        name = entity["text"].strip()
        if not name:
            continue
        if name.lower() in _NON_EMPLOYER_ORGANIZATIONS:
            continue
        if _is_org_fragment(name, names):
            continue
        usable.append(entity)
    return usable


def _is_low_quality_ner_experience(entry: ExperienceEntry) -> bool:
    company = (entry.company or "").strip()
    title = (entry.title or "").strip()
    if company and company.lower() in _NON_EMPLOYER_ORGANIZATIONS:
        return True
    if company and not title:
        return True
    return False


def _is_ner_experience_redundant(
    ner: ExperienceEntry,
    structured: ExperienceEntry,
) -> bool:
    ner_title = (ner.title or "").lower()
    structured_title = (structured.title or "").lower()
    ner_company = (ner.company or "").lower()
    structured_company = (structured.company or "").lower()

    if ner_title and structured_title and ner_title != structured_title:
        return False
    if not ner_title and structured_title:
        return False

    if ner_company and structured_company:
        if ner_company == structured_company:
            return True
        if ner_company in structured_company and len(structured_company) - len(ner_company) > 4:
            return True
    if not ner_company and structured_company and ner_title == structured_title:
        return True
    return False


def _is_ner_education_redundant(
    ner: EducationEntry,
    structured: EducationEntry,
) -> bool:
    ner_degree = (ner.degree or "").lower()
    structured_degree = (structured.degree or "").lower()
    ner_field = (ner.field or "").lower()
    structured_field = (structured.field or "").lower()
    ner_institution = (ner.institution or "").lower()
    structured_institution = (structured.institution or "").lower()

    if ner_institution and structured_institution and ner_institution != structured_institution:
        return False

    if ner_degree and structured_degree and ner_degree == structured_degree:
        if not ner_field or not structured_field or ner_field == structured_field:
            return True
    return False


def _filter_redundant_ner_claims(claims: Sequence[Claim]) -> list[Claim]:
    """Drop NER rows that duplicate or degrade structured experience/education claims."""
    structured_experience = [
        claim.value
        for claim in claims
        if claim.field == "experience"
        and claim.method is ExtractionMethod.STRUCTURED_PARSE
        and isinstance(claim.value, ExperienceEntry)
    ]
    structured_education = [
        claim.value
        for claim in claims
        if claim.field == "education"
        and claim.method is ExtractionMethod.STRUCTURED_PARSE
        and isinstance(claim.value, EducationEntry)
    ]

    filtered: list[Claim] = []
    for claim in claims:
        if claim.method is not ExtractionMethod.NER:
            filtered.append(claim)
            continue
        if claim.field == "experience" and isinstance(claim.value, ExperienceEntry):
            entry = claim.value
            if _is_low_quality_ner_experience(entry):
                continue
            if any(
                _is_ner_experience_redundant(entry, structured)
                for structured in structured_experience
            ):
                continue
        if claim.field == "education" and isinstance(claim.value, EducationEntry):
            entry = claim.value
            if entry.institution is None and entry.degree is not None:
                if any(
                    _is_ner_education_redundant(entry, structured)
                    for structured in structured_education
                ):
                    continue
        filtered.append(claim)
    return filtered


def _claims_from_entities(entities: Sequence[_GlinerEntity]) -> list[Claim]:
    """Convert GLiNER entities into claims, pairing nearby related spans."""
    ordered = sorted(entities, key=lambda entity: (entity["start"], entity["end"], entity["label"]))
    claims: list[Claim] = []
    method = ExtractionMethod.NER

    titles = [entity for entity in ordered if entity["label"].strip().lower() == "job title"]
    organizations = _usable_organizations([
        entity for entity in ordered if entity["label"].strip().lower() == "organization"
    ])
    universities = [entity for entity in ordered if entity["label"].strip().lower() == "university"]
    degrees = [entity for entity in ordered if entity["label"].strip().lower() == "degree"]

    used_orgs: set[int] = set()
    used_unis: set[int] = set()
    used_degrees: set[int] = set()
    used_titles: set[int] = set()

    for title_index, title in enumerate(titles):
        org, org_index = _closest_entity(title, organizations, used_orgs)
        title_text = title["text"].strip()
        raw = title["text"]
        if org is not None and org_index is not None:
            used_orgs.add(org_index)
            used_titles.add(title_index)
            raw = f"{title_text} @ {org['text'].strip()}"
            claims.append(_resume_claim(
                "experience",
                ExperienceEntry(title=title_text, company=org["text"].strip()),
                raw,
                method,
            ))
        else:
            used_titles.add(title_index)

    for uni_index, university in enumerate(universities):
        degree, degree_index = _closest_entity(university, degrees, used_degrees)
        institution = university["text"].strip()
        raw = university["text"]
        if degree is not None and degree_index is not None:
            used_degrees.add(degree_index)
            used_unis.add(uni_index)
            degree_text = degree["text"].strip()
            parsed_degree, field = _education_from_degree_text(degree_text)
            end_year = _education_year(degree_text)
            raw = f"{degree_text} @ {institution}"
            claims.append(_resume_claim(
                "education",
                EducationEntry(
                    institution=institution,
                    degree=parsed_degree,
                    field=field,
                    end_year=end_year,
                ),
                raw,
                method,
            ))
        else:
            used_unis.add(uni_index)

    for entity in ordered:
        label = entity["label"].strip().lower()
        if label in {"job title", "organization", "university", "degree"}:
            continue
        claim = _claim_from_entity(entity)
        if claim is not None:
            claims.append(claim)

    return claims


def _claim_from_entity(entity: _GlinerEntity) -> Claim | None:
    """Map one GLiNER entity to a resume claim, or None when the label is unknown."""
    raw = entity["text"]
    cleaned = raw.strip()
    if not cleaned:
        return None

    label = entity["label"].strip().lower()
    method = ExtractionMethod.NER

    if label == "person":
        return _resume_claim("full_name", cleaned, raw, method)
    if label == "certification":
        return _resume_claim("certifications", cleaned, raw, method)
    if label == "location":
        return _resume_claim("location", _location_from_text(cleaned), raw, method)
    return None


# --------------------------------------------------------------------------- #
# Extraction strategies                                                       #
# --------------------------------------------------------------------------- #


class ResumeExtractor(Protocol):
    """A strategy that turns resume text into claims."""

    def extract(self, text: str) -> list[Claim]:
        """Return the claims this strategy can derive from ``text``."""
        ...


class SectionExtractor:
    """Heading-based structured parsing (higher-trust structured parse)."""

    _method = ExtractionMethod.STRUCTURED_PARSE

    def extract(self, text: str) -> list[Claim]:
        claims: list[Claim] = []
        lines = _normalize_resume_text(text).splitlines()
        nonempty = [line.strip() for line in lines if line.strip()]

        full_name = _header_full_name(nonempty)
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


class GlinerExtractor:
    """Semantic entity extraction over the full resume text (NER)."""

    _method = ExtractionMethod.NER

    def extract(self, text: str) -> list[Claim]:
        if not text.strip():
            return []
        try:
            normalized = _normalize_resume_text(text)
            entities = _predict_entities(normalized)
        except Exception:
            logger.exception("GLiNER entity prediction failed")
            return []
        return _claims_from_entities(entities)


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
            (ProseExtractor(), SectionExtractor(), GlinerExtractor())
            if extractors is None
            else tuple(extractors)
        )

    def can_handle(self, path: Path) -> bool:
        """Handle resume document extensions."""
        return path.suffix.lower() in self._SUFFIXES

    def extract(self, path: Path) -> list[Claim]:
        """Read the resume and collect claims from every strategy.

        Reading failures propagate (so a garbage file is quarantined); a strategy
        that trips on odd formatting is isolated and degrades to fewer claims.
        """
        text = _normalize_resume_text(_read_text(path))
        claims: list[Claim] = []
        for extractor in self._extractors:
            try:
                claims.extend(extractor.extract(text))
            except Exception:
                continue
        return _filter_redundant_ner_claims(claims)


register_adapter(ResumeAdapter())
