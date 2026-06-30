"""Generate the Step-1 one-page technical design PDF for the Eightfold assignment."""

from __future__ import annotations

import io
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

AUTHOR_NAME = "Sai Sandeep R"
AUTHOR_EMAIL = "sandeep.5112004@gmail.com"
OUTPUT_NAME = f"{AUTHOR_NAME.replace(' ', '_')}_{AUTHOR_EMAIL}_Eightfold.pdf"


def _pipeline_figure_bytes() -> bytes:
  fig, ax = plt.subplots(figsize=(7.2, 1.35), dpi=200)
  ax.set_xlim(0, 10)
  ax.set_ylim(0, 3.2)
  ax.axis("off")

  # Source layer
  sources = [
    ("ATS JSON", 0.55, 2.35, "#BBDEFB"),
    ("Resume\nPDF/DOCX", 2.05, 2.35, "#BBDEFB"),
    ("GitHub\nAPI", 3.55, 2.35, "#BBDEFB"),
  ]
  for label, x, y, color in sources:
    box = FancyBboxPatch(
      (x, y),
      1.2,
      0.65,
      boxstyle="round,pad=0.04,rounding_size=0.08",
      facecolor=color,
      edgecolor="#546E7A",
      linewidth=0.8,
    )
    ax.add_patch(box)
    ax.text(x + 0.6, y + 0.325, label, ha="center", va="center", fontsize=7, fontweight="bold")

  # Arrows down to detect
  for x in [1.15, 2.65, 4.15]:
    ax.add_patch(
      FancyArrowPatch((x, 2.32), (x, 1.95), arrowstyle="-|>", mutation_scale=8, color="#78909C", lw=0.9)
    )

  # Core pipeline
  stages = [
    ("Detect", 0.35, 1.25, "#E3F2FD"),
    ("Extract\n→ Claims", 1.55, 1.25, "#E3F2FD"),
    ("Cluster\n(keys)", 2.95, 1.25, "#FFF3E0"),
    ("Fuse\nnormalize+merge", 4.35, 1.25, "#FFE0B2"),
    ("Canonical\nProfile", 5.95, 1.25, "#FFECB3"),
    ("Project\n(config)", 7.45, 1.25, "#C8E6C9"),
    ("Validate", 8.75, 1.25, "#C8E6C9"),
  ]
  for label, x, y, color in stages:
    w = 1.15 if "Extract" in label or "Fuse" in label else 1.0
    if label.startswith("Canonical"):
      w = 1.2
    box = FancyBboxPatch(
      (x, y),
      w,
      0.62,
      boxstyle="round,pad=0.03,rounding_size=0.06",
      facecolor=color,
      edgecolor="#455A64",
      linewidth=0.8,
    )
    ax.add_patch(box)
    ax.text(x + w / 2, y + 0.31, label, ha="center", va="center", fontsize=6.5, fontweight="bold")

  # Horizontal arrows between stages
  arrow_pairs = [
    (1.35, 1.56, 2.95),
    (2.7, 2.95, 4.35),
    (4.1, 4.35, 5.95),
    (5.7, 5.95, 7.45),
    (7.2, 7.45, 8.75),
    (8.5, 8.75, 9.75),
  ]
  for _, x1, x2 in arrow_pairs:
    ax.add_patch(
      FancyArrowPatch(
        (x1, 1.56),
        (x2, 1.56),
        arrowstyle="-|>",
        mutation_scale=7,
        color="#607D8B",
        lw=0.9,
        shrinkA=2,
        shrinkB=2,
      )
    )

  # Output
  out_box = FancyBboxPatch(
    (8.55, 0.15),
    1.25,
    0.55,
    boxstyle="round,pad=0.03,rounding_size=0.06",
    facecolor="#E8F5E9",
    edgecolor="#2E7D32",
    linewidth=1.0,
  )
  ax.add_patch(out_box)
  ax.text(9.175, 0.425, "JSON output\n+ violations", ha="center", va="center", fontsize=6.5, fontweight="bold")
  ax.add_patch(
    FancyArrowPatch((9.175, 1.25), (9.175, 0.72), arrowstyle="-|>", mutation_scale=8, color="#2E7D32", lw=1)
  )

  # Quarantine branch
  q_box = FancyBboxPatch(
    (0.35, 0.15),
    1.35,
    0.5,
    boxstyle="round,pad=0.03,rounding_size=0.06",
    facecolor="#FFEBEE",
    edgecolor="#C62828",
    linewidth=0.8,
    linestyle="--",
  )
  ax.add_patch(q_box)
  ax.text(1.025, 0.4, "Quarantine\n(bad files)", ha="center", va="center", fontsize=6.2, color="#B71C1C")
  ax.add_patch(
    FancyArrowPatch((1.0, 1.25), (1.025, 0.68), arrowstyle="-|>", mutation_scale=7, color="#C62828", lw=0.8, linestyle="dashed")
  )

  ax.text(
    5.0,
    2.95,
    "Hourglass: many sources → Claim[] → one CanonicalProfile → many projected views",
    ha="center",
    va="center",
    fontsize=7.5,
    fontstyle="italic",
    color="#37474F",
  )

  buf = io.BytesIO()
  fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.05, transparent=False, facecolor="white")
  plt.close(fig)
  buf.seek(0)
  return buf.getvalue()


def build_pdf(output_path: Path) -> None:
  doc = SimpleDocTemplate(
    str(output_path),
    pagesize=letter,
    leftMargin=0.42 * inch,
    rightMargin=0.42 * inch,
    topMargin=0.32 * inch,
    bottomMargin=0.28 * inch,
  )

  section = ParagraphStyle(
    "Section",
    fontName="Helvetica-Bold",
    fontSize=7.8,
    leading=9,
    spaceBefore=2,
    spaceAfter=1,
    textColor=colors.HexColor("#0D47A1"),
  )
  body = ParagraphStyle(
    "Body",
    fontName="Helvetica",
    fontSize=6.6,
    leading=7.8,
    spaceAfter=0,
    textColor=colors.HexColor("#212121"),
  )
  bullet = ParagraphStyle("Bullet", parent=body, leftIndent=7, bulletIndent=0)

  story: list = []
  story.append(
    Paragraph(
      '<font size="12" color="#1565C0"><b>Multi-Source Candidate Data Transformer</b></font>'
      ' &nbsp;|&nbsp; <font size="7" color="#546E7A">Technical Design (Step 1)</font>',
      body,
    )
  )
  story.append(
    Paragraph(
      f'<font size="7">{AUTHOR_NAME} · {AUTHOR_EMAIL} · Eightfold Engineering Intern (Jul–Dec 2026)</font>',
      body,
    )
  )
  story.append(Spacer(1, 2))
  story.append(
    Paragraph(
      "<b>Problem.</b> Recruiters receive fragmented, inconsistent candidate data across ATS exports, "
      "resumes, and public profiles. Build a deterministic pipeline that fuses sources into one "
      "<i>trustworthy</i> canonical profile per person — with provenance and per-field confidence — "
      "then projects to arbitrary consumer schemas via runtime JSON config (no core-code changes).",
      body,
    )
  )
  story.append(Spacer(1, 3))

  diagram = Image(io.BytesIO(_pipeline_figure_bytes()), width=7.25 * inch, height=1.35 * inch)
  story.append(diagram)
  story.append(Spacer(1, 4))

  col_w = (letter[0] - 0.84 * inch) / 2
  left = [
    Paragraph("Canonical Output Schema", section),
    Paragraph(
      "Fixed <b>CanonicalProfile</b> (internal verdict, Pydantic-validated): "
      "<b>candidate_id</b> (SHA-1 of strongest key: email → profile URL → name|phones); "
      "<b>full_name</b>; <b>emails[]</b>, <b>phones[]</b> (E.164 via phonenumbers); "
      "<b>location</b> {city, region, country} (country ISO-3166 α-2); "
      "<b>links</b> {linkedin, github, portfolio, other[]}; <b>headline</b>, <b>years_experience</b>; "
      "<b>skills[]</b> {name, confidence, sources[]} (canonical alias map); "
      "<b>experience[]</b> / <b>education[]</b> (dates YYYY-MM); "
      "<b>provenance[]</b> {field, source, method, note}; <b>overall_confidence</b>.",
      body,
    ),
    Paragraph("Merge &amp; Conflict Resolution", section),
    Paragraph(
      "<b>Identity clustering</b> (resolve.py): union-find on exact keys — normalized email, E.164 phone, "
      "canonical URL. No fuzzy name matching; keyless records stay isolated.",
      body,
    ),
    Paragraph(
      "<b>Scalars</b> (name, headline, years): group by normalized value; winner = noisy-OR confidence "
      "(1−∏(1−c), cap 0.99); tie-break by source priority (ATS &gt; resume_section &gt; GitHub &gt; prose). "
      "High-trust disagreement (loser ≥0.7) → −0.1 penalty. Below τ=0.5 honesty gate → null "
      "(note: withheld_low_confidence).",
      body,
    ),
    Paragraph(
      "<b>Lists</b> (emails, phones, skills): union of normalize_ok values, deduped, sorted. "
      "<b>Experience/education:</b> DIRECT_MAP (ATS) claims authoritative; resume parses superseded. "
      "Dedup by (company, title) / (institution, degree, end_year).",
      body,
    ),
    Paragraph(
      "<b>Confidence model:</b> claim score = source_trust × method_reliability; corroboration via noisy-OR; "
      "overall = mean of core fields. Principle: wrong-but-confident is worse than honestly-empty.",
      body,
    ),
  ]

  right = [
    Paragraph("Runtime Custom-Output Config", section),
    Paragraph(
      "Clean separation: <b>CanonicalProfile</b> is the adjudicated internal record; "
      "<b>project.py</b> is a field-agnostic projection engine driven by JSON config. "
      "<b>fields[]</b>: output path, optional <b>from</b> (dotted canonical path, e.g. <i>emails[0]</i>, "
      "<i>skills[].name</i>), type, required, normalize token (E164, canonical, etc.). "
      "Toggles: <b>include_confidence</b>, <b>include_provenance</b> (metadata sidecar, not in bare values). "
      "<b>on_missing:</b> null | omit | error. Empty fields[] → full canonical dump. "
      "<b>validate.py</b> checks projected values against FieldSpec before return.",
      body,
    ),
    Paragraph("Edge Cases", section),
    Paragraph("• <b>Bad/unrecognized source:</b> quarantine record; pipeline continues for other inputs.", bullet),
    Paragraph(
      "• <b>Low-confidence lone scalar</b> (e.g. regex years from resume prose): withheld as null.", bullet
    ),
    Paragraph("• <b>ATS vs resume conflict:</b> ATS DIRECT_MAP wins for structured experience/education.", bullet),
    Paragraph("• <b>Corroborating contact info</b> across sources: deduped, confidence boosted via noisy-OR.", bullet),
    Paragraph(
      "• <b>Descoped:</b> LinkedIn adapter, recruiter CSV/notes, fuzzy name matching (NOTES enum only).",
      bullet,
    ),
    Paragraph("Interfaces", section),
    Paragraph(
      "CLI (<i>python -m app.cli</i>) and FastAPI + minimal web UI. Pure deterministic core after extraction. "
      "Gold-tested on Priya Sharma ATS+resume fusion; custom config remaps primary_email, skill_names[].",
      body,
    ),
  ]

  table = Table([[left, right]], colWidths=[col_w, col_w])
  table.setStyle(
    TableStyle(
      [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 10),
        ("RIGHTPADDING", (1, 0), (1, 0), 0),
      ]
    )
  )
  story.append(table)

  doc.build(story)


if __name__ == "__main__":
  root = Path(__file__).resolve().parent.parent
  out = root / OUTPUT_NAME
  build_pdf(out)
  print(f"Wrote {out}")
