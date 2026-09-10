"""PDF knowledge extractor for BARQ Systems Operations Manual (Section 6 runbooks).

Extracts KB0001 through KB0010 from data/barq-system-kb.pdf, converts them into
clean canonical Markdown, enriches with metadata and security tiers, and validates
against the Article schema.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from app.models.knowledge import Article, SecurityLevel, WorkflowState

SECURITY_MAPPING: dict[str, SecurityLevel] = {
    "corporate-vpn": SecurityLevel.INTERNAL,
    "corporate-email": SecurityLevel.INTERNAL,
    "file-services": SecurityLevel.INTERNAL,
    "print-services": SecurityLevel.INTERNAL,
    "identity": SecurityLevel.INTERNAL,
    "corporate-wifi": SecurityLevel.INTERNAL,
    "endpoint": SecurityLevel.RESTRICTED,
    "sap-erp": SecurityLevel.RESTRICTED,
    "order-processing": SecurityLevel.RESTRICTED,
}

CATEGORY_MAPPING: dict[str, str] = {
    "corporate-vpn": "network",
    "corporate-email": "software",
    "file-services": "network",
    "print-services": "hardware",
    "identity": "inquiry",
    "endpoint": "hardware",
    "sap-erp": "software",
    "corporate-wifi": "network",
    "order-processing": "database",
}


def _clean_paragraph(raw: str) -> str:
    """Join soft-wrapped lines into flowing text while preserving numbered steps."""
    lines = [line.strip() for line in raw.strip().split("\n") if line.strip()]
    merged: list[str] = []
    for line in lines:
        if re.match(r"^\d+\.", line):
            merged.append("\n" + line)
        else:
            if merged and not merged[-1].endswith("\n"):
                merged.append(" " + line)
            else:
                merged.append(line)
    return "".join(merged).strip()


class PDFKnowledgeExtractor:
    """Extracts runbooks from Section 6 of the BARQ Operations Manual."""

    def __init__(self, pdf_path: str | Path = "data/barq-system-kb.pdf") -> None:
        self.pdf_path = Path(pdf_path)

    def extract_raw_text(self, first_page: int = 17, last_page: int = 24) -> str:
        """Extract text from the specified page range using pdftotext."""
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF not found at {self.pdf_path}")

        cmd = [
            "pdftotext",
            "-f",
            str(first_page),
            "-l",
            str(last_page),
            str(self.pdf_path),
            "-",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return res.stdout

    def clean_text(self, text: str) -> str:
        """Strip running headers, footers, and page numbers."""
        cleaned = re.sub(
            r"Edition\s+[\d\.]+\s+\d+\s+of\s+\d+\s+·\s+BARQ\s+Systems\s+·"
            r"\s+IT\s+Service\s+Operations\s+Manual\s+INTERNAL\s+DOCUMENT",
            "",
            text,
            flags=re.MULTILINE,
        )
        cleaned = re.sub(
            r"BARQ\s+Systems\s+·\s+IT\s+Service\s+Operations\s+Manual\s+INTERNAL\s+DOCUMENT",
            "",
            cleaned,
        )
        return cleaned

    def extract_articles(self) -> list[Article]:
        """Parse all Section 6 articles into validated Article models."""
        raw = self.extract_raw_text()
        clean = self.clean_text(raw)
        articles: list[Article] = []

        standard_sections = [
            (
                "6.4 KB0001",
                "6.5 KB0002",
                "KB0001",
                "VPN authentication fails after a password change",
            ),
            (
                "6.5 KB0002",
                "6.6 KB0003",
                "KB0002",
                "Outlook shows Disconnected and no mail is delivered",
            ),
            (
                "6.6 KB0003",
                "6.7 KB0004",
                "KB0003",
                "Mapped shared drive is missing after sign-in",
            ),
            ("6.7 KB0004", "6.8 KB0005", "KB0004", "Print jobs queue but nothing prints"),
            (
                "6.8 KB0005",
                "6.9 KB0006",
                "KB0005",
                "Account is locked after repeated failed sign-ins",
            ),
            (
                "6.9 KB0006",
                "6.10 KB0007",
                "KB0006",
                "Multi-factor authentication after a lost or replaced device",
            ),
            (
                "6.10 KB0007",
                "6.11 KB0008",
                "KB0007",
                "Laptop performance degrades after a system update",
            ),
            (
                "6.11 KB0008",
                "6.12 KB0009",
                "KB0008",
                "SAP GUI connection times out with RFC_ERROR_COMMUNICATION",
            ),
            (
                "6.12 KB0009",
                "6.13 KB0010",
                "KB0009",
                "Wi-Fi drops repeatedly on the 5 GHz corporate network",
            ),
        ]

        for start_tag, end_tag, kb_id, title in standard_sections:
            s_idx = clean.find(start_tag)
            e_idx = clean.find(end_tag) if end_tag else len(clean)
            if s_idx == -1:
                continue
            section_text = clean[s_idx:e_idx]
            art = self._parse_standard_section(kb_id, title, section_text)
            articles.append(art)

        sec10_idx = clean.find("6.13 KB0010")
        if sec10_idx != -1:
            sec10_text = clean[sec10_idx:]
            articles.extend(self._parse_kb0010_versions(sec10_text))

        return articles

    def _parse_standard_section(self, kb_id: str, title: str, text: str) -> Article:
        ver_m = re.search(r"Version\s+(\d+)", text)
        ver = f"{ver_m.group(1)}.0" if ver_m else "1.0"

        srv_m = re.search(r"Service\s+([a-z0-9\-]+)", text)
        service = srv_m.group(1) if srv_m else "identity"
        category = CATEGORY_MAPPING.get(service, "inquiry")
        security = SECURITY_MAPPING.get(service, SecurityLevel.INTERNAL)

        sym_m = re.search(r"Symptom\.\s*(.*?)(?=Cause\.|$)", text, re.DOTALL)
        cause_m = re.search(r"Cause\.\s*(.*?)(?=Resolution\.|$)", text, re.DOTALL)
        res_m = re.search(r"Resolution\.\s*(.*?)(?=Escalation\.|$)", text, re.DOTALL)
        esc_m = re.search(r"Escalation\.\s*(.*?)(?=\Z)", text, re.DOTALL)

        symptom = _clean_paragraph(sym_m.group(1)) if sym_m else ""
        cause = _clean_paragraph(cause_m.group(1)) if cause_m else ""
        resolution = _clean_paragraph(res_m.group(1)) if res_m else ""
        escalation = _clean_paragraph(esc_m.group(1)) if esc_m else ""

        if "." in symptom[:200]:
            short_desc = symptom[:200].rsplit(".", 1)[0] + "."
        else:
            short_desc = symptom[:100]

        if len(short_desc) < 10:
            short_desc = title[:200]

        content = (
            f"# {title}\n\n"
            f"## Symptom\n{symptom}\n\n"
            f"## Cause\n{cause}\n\n"
            f"## Resolution\n{resolution}\n\n"
            f"## Escalation\n{escalation}\n"
        )

        return Article(
            base_id=kb_id,
            version=ver,
            article_id=f"{kb_id}-v{ver}",
            title=title,
            short_description=short_desc,
            category=category,
            service=service,
            workflow_state=WorkflowState.PUBLISHED,
            security_level=security,
            content=content,
        )

    def _parse_kb0010_versions(self, text: str) -> list[Article]:
        results: list[Article] = []
        v1_idx = text.find("Version 1")
        v2_idx = text.find("Version 2")

        if v1_idx != -1 and v2_idx != -1:
            v1_text = text[v1_idx:v2_idx]
            v2_text = text[v2_idx:]

            # Version 1 (Retired)
            sym_v1_m = re.search(r"Symptom\.\s*(.*?)(?=Cause\.|$)", v1_text, re.DOTALL)
            cause_v1_m = re.search(r"Cause\.\s*(.*?)(?=Resolution\.|$)", v1_text, re.DOTALL)
            res_v1_m = re.search(r"Resolution\.\s*(.*?)(?=Escalation\.|$)", v1_text, re.DOTALL)
            esc_v1_m = re.search(r"Escalation\.\s*(.*?)(?=Why this revision|$)", v1_text, re.DOTALL)
            danger_m = re.search(
                r"Why this revision is dangerous, not merely outdated\s*(.*?)(?=\Z)",
                v1_text,
                re.DOTALL,
            )

            sym_v1 = _clean_paragraph(sym_v1_m.group(1)) if sym_v1_m else ""
            cause_v1 = _clean_paragraph(cause_v1_m.group(1)) if cause_v1_m else ""
            res_v1 = _clean_paragraph(res_v1_m.group(1)) if res_v1_m else ""
            esc_v1 = _clean_paragraph(esc_v1_m.group(1)) if esc_v1_m else ""
            danger_v1 = _clean_paragraph(danger_m.group(1)) if danger_m else ""

            content_v1 = (
                "# Order service connection pool exhaustion (RETIRED)\n\n"
                f"> [!CAUTION]\n> {danger_v1}\n\n"
                f"## Symptom\n{sym_v1}\n\n"
                f"## Cause\n{cause_v1}\n\n"
                f"## Resolution\n{res_v1}\n\n"
                f"## Escalation\n{esc_v1}\n"
            )

            results.append(
                Article(
                    base_id="KB0010",
                    version="1.0",
                    article_id="KB0010-v1.0",
                    title="Order service connection pool exhaustion (Retired)",
                    short_description=(
                        "Historical retired runbook for order service pool exhaustion. "
                        "Do not apply."
                    ),
                    category="database",
                    service="order-processing",
                    workflow_state=WorkflowState.RETIRED,
                    security_level=SecurityLevel.RESTRICTED,
                    content=content_v1,
                )
            )

            # Version 2 (Published)
            sym_v2_m = re.search(r"Symptom\.\s*(.*?)(?=Cause\.|$)", v2_text, re.DOTALL)
            cause_v2_m = re.search(r"Cause\.\s*(.*?)(?=Resolution\.|$)", v2_text, re.DOTALL)
            res_v2_m = re.search(r"Resolution\.\s*(.*?)(?=Escalation\.|$)", v2_text, re.DOTALL)
            esc_v2_m = re.search(r"Escalation\.\s*(.*?)(?=\Z|Edition)", v2_text, re.DOTALL)

            sym_v2 = _clean_paragraph(sym_v2_m.group(1)) if sym_v2_m else ""
            cause_v2 = _clean_paragraph(cause_v2_m.group(1)) if cause_v2_m else ""
            res_v2 = _clean_paragraph(res_v2_m.group(1)) if res_v2_m else ""
            esc_v2 = _clean_paragraph(esc_v2_m.group(1)) if esc_v2_m else ""

            content_v2 = (
                "# Order service connection pool exhaustion\n\n"
                f"## Symptom\n{sym_v2}\n\n"
                f"## Cause\n{cause_v2}\n\n"
                f"## Resolution\n{res_v2}\n\n"
                f"## Escalation\n{esc_v2}\n"
            )

            results.append(
                Article(
                    base_id="KB0010",
                    version="2.0",
                    article_id="KB0010-v2.0",
                    title="Order service connection pool exhaustion",
                    short_description=(
                        "Resolution procedure for order service database connection "
                        "pool exhaustion via change-controlled drain."
                    ),
                    category="database",
                    service="order-processing",
                    workflow_state=WorkflowState.PUBLISHED,
                    security_level=SecurityLevel.RESTRICTED,
                    content=content_v2,
                )
            )

        return results

    def save_corpus(self, output_path: str | Path = "data/corpus/barq_kb_articles.json") -> Path:
        """Extract articles and write to a JSON file."""
        articles = self.extract_articles()
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        data = [art.model_dump(mode="json") for art in articles]
        out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return out
