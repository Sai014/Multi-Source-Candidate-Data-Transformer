"""Read resume text from PDF, DOCX, and plain-text files."""

from __future__ import annotations

from pathlib import Path


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


def read_resume_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _read_pdf(path)
    if suffix == ".docx":
        return _read_docx(path)
    return path.read_text(encoding="utf-8", errors="replace")
