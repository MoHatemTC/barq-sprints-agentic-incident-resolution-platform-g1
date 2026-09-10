"""Deterministic parser for the BARQ IT Service Operations Manual PDF.

Extracts Section 6 (Knowledge base articles) into canonical Article model instances
using `pdftotext -layout` (poppler-utils) with layout preservation, header/footer
stripping, hyphen-join preservation, and metadata grid normalization.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.models.knowledge import Article, SecurityLevel, WorkflowState

SECURITY_TIERS: dict[str, SecurityLevel] = {
    "KB0001": SecurityLevel.INTERNAL,
    "KB0002": SecurityLevel.INTERNAL,
    "KB0003": SecurityLevel.INTERNAL,
    "KB0004": SecurityLevel.RESTRICTED,
    "KB0005": SecurityLevel.INTERNAL,
    "KB0006": SecurityLevel.INTERNAL,
    "KB0007": SecurityLevel.RESTRICTED,
    "KB0008": SecurityLevel.RESTRICTED,
    "KB0009": SecurityLevel.INTERNAL,
    "KB0010": SecurityLevel.RESTRICTED,
}

STEP_MARKER_RE = re.compile(r"(?m)^\s*(\d{1,3})\.\s+")


@dataclass
class ExtractionReport:
    """Sanitized report containing statistics and warnings for the extraction run."""

    total_records: int = 0
    published_count: int = 0
    retired_count: int = 0
    warnings: list[str] = field(default_factory=list)
    parsed_articles: list[str] = field(default_factory=list)


def check_pdftotext_installed() -> None:
    """Verify that pdftotext (poppler-utils) is available on the system PATH."""
    if shutil.which("pdftotext") is None:
        raise RuntimeError(
            "pdftotext is not installed or not in PATH. "
            "Please install poppler-utils (e.g. apt-get install poppler-utils)."
        )


def extract_raw_text_from_pdf(pdf_path: Path, start_page: int = 18, end_page: int = 24) -> str:
    """Extract layout-preserved text from the PDF using pdftotext."""
    check_pdftotext_installed()
    if not pdf_path.exists():
        raise FileNotFoundError(f"Source PDF not found at {pdf_path}")

    cmd = [
        "pdftotext",
        "-layout",
        "-f",
        str(start_page),
        "-l",
        str(end_page),
        str(pdf_path),
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout


def strip_page_headers_and_footers(text: str) -> str:
    """Remove repeating running headers and footers from pdftotext output."""
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        # Running top header
        if "BARQ Systems · IT Service Operations Manual" in line and "INTERNAL DOCUMENT" in line:
            continue
        # Running bottom footer
        if re.search(r"Edition 4\.0\s+\d+\s+of\s+52", line):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def assert_zero_loss(source: str, rebuilt: str) -> None:
    """Reassembled steps must equal the source once whitespace is normalized.

    Any other difference means the splitter dropped or duplicated text, so a
    corrupted runbook must never be emitted silently.
    """
    normalized_source = re.sub(r"\s+", " ", source).strip()
    normalized_rebuilt = re.sub(r"\s+", " ", rebuilt).strip()
    if normalized_rebuilt != normalized_source:
        raise ValueError(
            "Resolution step splitting lost or duplicated text; "
            "refusing to emit corrupted Markdown."
        )


def split_resolution_steps(clean_res: str) -> str:
    """Split a Resolution section into one Markdown line per numbered step.

    A candidate marker is only accepted when it continues the 1, 2, 3... step
    sequence, so numbers that merely wrap to a line start (dates, versions,
    error codes — e.g. "…outage on 14 March\\n2026.") stay inside the step they
    belong to. A zero-loss check reassembles the text and raises if anything
    was dropped or duplicated, so a corrupted runbook can never be emitted.
    """
    expected = 1
    accepted: list[tuple[int, int, int]] = []  # (step number, marker start, body start)
    for match in STEP_MARKER_RE.finditer(clean_res):
        if int(match.group(1)) == expected:
            accepted.append((expected, match.start(1), match.end()))
            expected += 1

    if not accepted:
        return clean_res

    parts: list[str] = []
    preamble = clean_res[: accepted[0][1]].strip()
    if preamble:
        parts.append(re.sub(r"\s+", " ", preamble))

    for i, (number, _, body_start) in enumerate(accepted):
        body_end = accepted[i + 1][1] if i + 1 < len(accepted) else len(clean_res)
        step_body = re.sub(r"\s+", " ", clean_res[body_start:body_end]).strip()
        parts.append(f"{number}. {step_body}")

    rebuilt = "\n".join(parts)
    assert_zero_loss(clean_res, rebuilt)
    return rebuilt


def parse_article_block(block: str, report: ExtractionReport | None = None) -> Article:
    """Parse a single raw text article block into a validated Article model."""
    # 1. Identify article number & title banner
    banner_m = re.search(r"^\s*(KB\d{4})\s{2,}([A-Z0-9\s—·*\-_]+)$", block, re.MULTILINE)
    if not banner_m:
        raise ValueError("Could not find article header banner (KB000X <TITLE>) in text block")

    article_number = banner_m.group(1)
    raw_title = banner_m.group(2).strip()
    title = re.sub(r"\s*·\s*\*\*RETIRED\*\*", "", raw_title).strip()
    title = " ".join(title.split())

    # 2. Metadata Grid parsing
    state_m = re.search(r"State\s+([A-Za-z]+)", block)
    raw_state = state_m.group(1).lower() if state_m else "published"
    workflow_state = WorkflowState(raw_state)

    version_m = re.search(r"Version\s+(\d+)", block)
    raw_ver = version_m.group(1) if version_m else "1"
    version = f"{raw_ver}.0"

    service_m = re.search(r"Service\s+([a-z-]+)", block)
    service = service_m.group(1) if service_m else ""
    category_m = re.search(r"Category\s+([a-z-]+)", block)
    category = category_m.group(1) if category_m else ""

    # The KB0010 grids carry no Service/Category cells; default rather than
    # emit empty values, but say so in the report instead of doing it silently.
    if article_number == "KB0010":
        if not service:
            service = "order-processing"
            if report is not None:
                report.warnings.append(
                    "KB0010: no Service cell in grid; defaulted to 'order-processing'."
                )
        if not category:
            category = "software"
            if report is not None:
                report.warnings.append(
                    "KB0010: no Category cell in grid; defaulted to 'software'."
                )

    owner_m = re.search(r"Owner\s+([^\n]+)", block)
    owner = owner_m.group(1).strip() if owner_m else None
    if owner:
        owner = re.split(r"\s{4,}|Author", owner)[0].strip()

    author_m = re.search(r"Author\s+([^\n]+)", block)
    author = author_m.group(1).strip() if author_m else None
    if author:
        author = re.split(r"\s{4,}", author)[0].strip()

    # KB0010-v2 style grid has no Author label; the author rides in the Owner
    # cell separated by a middle dot: "Owner  Platform Engineering · K. Selim".
    if owner and "·" in owner and not author:
        owner, _, dot_author = owner.partition("·")
        owner, author = owner.strip(), dot_author.strip() or None

    reviewed_m = re.search(r"Reviewed\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})", block)
    reviewed_on = reviewed_m.group(1) if reviewed_m else None

    # Related records: PRB0040012, INC0010023, CHG0030455, etc.
    related_matches = re.findall(r"\b(?:PRB|INC|CHG|RITM|KE|MIR)[\w-]+\b", block)
    related_records = list(dict.fromkeys(related_matches))

    # 3. Body section extraction
    body_parts: list[str] = []

    # Symptom
    symptom_m = re.search(
        r"Symptom\.\s+(.*?)(?=\n\s*(?:Cause\.|Resolution\.|Escalation\.|\Z))",
        block,
        re.DOTALL,
    )
    symptom_text = ""
    if symptom_m:
        raw_symptom = symptom_m.group(1).strip()
        clean_symptom = re.sub(r"-\n\s*", "-", raw_symptom)
        clean_symptom = re.sub(r"\n\s*", " ", clean_symptom).strip()
        symptom_text = clean_symptom
        body_parts.append(f"## Symptom\n\n{clean_symptom}")

    # Cause
    cause_m = re.search(
        r"Cause\.\s+(.*?)(?=\n\s*(?:Resolution\.|Escalation\.|\Z))",
        block,
        re.DOTALL,
    )
    if cause_m:
        raw_cause = cause_m.group(1).strip()
        clean_cause = re.sub(r"-\n\s*", "-", raw_cause)
        clean_cause = re.sub(r"\n\s*", " ", clean_cause).strip()
        body_parts.append(f"## Cause\n\n{clean_cause}")

    # Resolution
    res_m = re.search(
        r"Resolution\.\s+(.*?)(?=\n\s*(?:Escalation\.|\Z|Why this revision))",
        block,
        re.DOTALL,
    )
    if res_m:
        raw_res = res_m.group(1).strip()
        clean_res = re.sub(r"-\n\s*", "-", raw_res)
        body_parts.append("## Resolution\n\n" + split_resolution_steps(clean_res))

    # Escalation
    esc_m = re.search(
        r"Escalation\.\s+(.*?)(?=\n\s*(?:Why this revision|\Z))",
        block,
        re.DOTALL,
    )
    if esc_m:
        raw_esc = esc_m.group(1).strip()
        clean_esc = re.sub(r"-\n\s*", "-", raw_esc)
        clean_esc = re.sub(r"\n\s*", " ", clean_esc).strip()
        body_parts.append(f"## Escalation\n\n{clean_esc}")

    # Warning for KB0010 v1 retired variant
    warn_m = re.search(
        r"Why this revision is dangerous.*?\n\s*(.*?)(?=\n\s*Version|\Z)",
        block,
        re.DOTALL,
    )
    if warn_m:
        raw_warn = warn_m.group(1).strip()
        clean_warn = re.sub(r"-\n\s*", "-", raw_warn)
        clean_warn = re.sub(r"\n\s*", " ", clean_warn).strip()
        body_parts.append(f"## Warning\n\nWhy this revision is dangerous: {clean_warn}")

    body = "\n\n".join(body_parts)

    # 4. Short description: first sentence of symptom (capped at 255)
    sentences = re.split(r"(?<=[.!?])\s+", symptom_text)
    short_desc = sentences[0] if sentences else title
    if len(short_desc) > 255:
        short_desc = short_desc[:252] + "..."

    security_level = SECURITY_TIERS.get(article_number, SecurityLevel.INTERNAL)

    return Article(
        article_number=article_number,
        version=version,
        title=title,
        body=body,
        short_description=short_desc,
        category=category,
        service=service,
        workflow_state=workflow_state,
        security_level=security_level,
        owner=owner,
        author=author,
        reviewed_on=reviewed_on,
        related_records=related_records,
    )


def extract_articles_from_cleaned_text(text: str) -> tuple[list[Article], ExtractionReport]:
    """Parse all Section 6 articles from pre-cleaned pdftotext string."""
    raw_sections = re.split(r"\n(?=6\.\d+\s+KB\d{4}|Version [12]\s+—\s+)", text)
    report = ExtractionReport()

    articles: list[Article] = []
    for s in raw_sections:
        # Check for Section 6.3 scan note
        if "6.3 KB0005 — the archived scan" in s:
            report.warnings.append(
                "KB0005 pre-2025 version exists as an archived scan (retained for audit only; "
                "published version in 6.8 parsed successfully)."
            )
            continue
        # Preamble to KB0010 (Section 6.13 header)
        if "6.13 KB0010 — Order service connection pool exhaustion" in s and "Version 1" not in s:
            continue
        if "KB000" in s or "KB0010" in s:
            art = parse_article_block(s, report)
            articles.append(art)
            report.parsed_articles.append(art.unique_key)
            if art.workflow_state == WorkflowState.RETIRED:
                report.retired_count += 1
            else:
                report.published_count += 1

    report.total_records = len(articles)
    return articles, report


def extract_barq_manual_articles(
    pdf_path: Path,
    start_page: int = 18,
    end_page: int = 24,
) -> tuple[list[Article], ExtractionReport]:
    """Extract and parse all knowledge base articles from the BARQ Operations Manual PDF."""
    raw_text = extract_raw_text_from_pdf(pdf_path, start_page=start_page, end_page=end_page)
    cleaned = strip_page_headers_and_footers(raw_text)
    return extract_articles_from_cleaned_text(cleaned)
