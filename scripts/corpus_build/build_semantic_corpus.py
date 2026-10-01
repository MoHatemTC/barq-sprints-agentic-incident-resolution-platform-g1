"""Build a Qdrant-ready semantic corpus from manual_markdown_sections.json.

The extraction scripts emit sections as structured blocks — raw table rows
arrays, headers, row/column counts, metadata — plus OCR-era fields
(ocr_confidence, reliability_note). None of that is meaningful to an
embedding model: Qdrant stores one text string per point and everything
else is payload noise.

This script flattens each section to a single `text` field:

* text blocks pass through unchanged,
* table blocks are rendered as semantic lines:
    - with headers:    "HEADER: cell | HEADER: cell"
    - without headers: "row label: rest of the row" (first column is the label)

and drops blocks/ocr_confidence/reliability_note/metadata/row_count/
column_count entirely. The relationships map is copied through unchanged so
chunking can still attach related-record ids.

Output: data/corpus/manual_semantic_sections.json
    {
      "sections": [{section_id, section_number, title, text, content_type, pages}],
      "relationships": {"forward": {...}, "reverse": {...}}
    }

Run with: .venv/bin/python scripts/build_semantic_corpus.py
"""

from __future__ import annotations

import json
from pathlib import Path

from app.models.manual_section import ManualSection, TableBlock, TextBlock

SOURCE_PATH = Path("data/corpus/manual_markdown_sections.json")
OUTPUT_PATH = Path("data/corpus/manual_semantic_sections.json")


def render_table(block: TableBlock) -> str:
    """Render a table block as semantic text an embedding model can read."""
    if block.headers:
        return block.to_semantic_text()

    # No headers: the first column is the row's label.
    lines = []
    for row in block.rows:
        cells = [c.strip() for c in row if c.strip()]
        if not cells:
            continue
        if len(cells) > 1:
            lines.append(f"{cells[0]}: {' | '.join(cells[1:])}")
        else:
            lines.append(cells[0])
    return "\n".join(lines)


def section_to_text(section: ManualSection) -> str:
    """Flatten a section's blocks into one semantic text string."""
    parts = []
    for block in section.blocks:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        elif isinstance(block, TableBlock):
            parts.append(render_table(block))
    return "\n\n".join(parts)


def main() -> None:
    raw = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    sections = [ManualSection.model_validate(item) for item in raw["sections"]]

    output_sections = []
    for section in sections:
        text = section_to_text(section)
        if not text.strip():
            raise ValueError(f"{section.section_id}: rendered text is empty")
        output_sections.append(
            {
                "section_id": section.section_id,
                "section_number": section.section_number,
                "title": section.title,
                "text": text,
                "content_type": section.content_type.value,
                "pages": list(section.pages),
            }
        )

    output = {
        "sections": output_sections,
        "relationships": raw.get("relationships", {"forward": {}, "reverse": {}}),
    }
    OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    fields_before = sorted({k for s in raw["sections"] for k in s})
    print(f"wrote {OUTPUT_PATH}")
    print(f"  sections: {len(output_sections)}")
    print(f"  dropped:  {', '.join(k for k in fields_before if k not in output_sections[0])}")
    print(f"  kept:     {', '.join(output_sections[0])}")

    sample = next(s for s in output_sections if s["section_number"] == "1.1")
    print("\nsample — section 1.1 'Purpose and audience', table now rendered as:")
    print("-" * 72)
    print(sample["text"].split("\n\n", 1)[1][:500])


if __name__ == "__main__":
    main()
