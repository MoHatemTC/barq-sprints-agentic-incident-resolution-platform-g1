from __future__ import annotations

import re
from dataclasses import dataclass

FORM_FEED = "\f"
_PAGE_SEPARATOR = f"\n{FORM_FEED}\n"

# Number component: dotted numeric ("3.4", "10.1.2")
_NUMBER_PATTERN = r"(?:(?:\d+\.)+\d*|\d+|[A-Z](?:\.\d+)*)"

SECTION_HEADING_RE = re.compile(
    r"^[ \t]*"  # leading whitespace on the line
    rf"({_NUMBER_PATTERN})"  # group 1: section number
    r"[ \t]{1,}"  # >=1 spaces distinguishes a heading from prose/lists
    r"([A-Z][^\n]{2,})"  # group 2: title, starts uppercase, >=3 chars
    r"[ \t]*$",  # trailing whitespace, end of line
    re.MULTILINE,
)
_TOC_LEADER_RE = re.compile(r"\.{3,}\s*\d+\s*$")

# A defensive second gate: a footer fragment like "of 52" that slipped past
# header/footer stripping must never be read as a section title.
_FOOTER_FRAGMENT_RE = re.compile(r"^\s*(?:of\s+\d+|page\s+\d+)\s*$", re.IGNORECASE)

_MAX_HEADING_WORDS = 10
_MAX_HEADING_CHARS = 80


def _looks_like_prose_sentence(title: str) -> bool:
    if len(title) > _MAX_HEADING_CHARS:
        return True
    if title.rstrip().endswith((".", ",", ";")):
        return True
    if len(title.split()) > _MAX_HEADING_WORDS:
        return True
    return False


@dataclass(frozen=True)
class DetectedHeading:
    """A section heading detected in a PDF page's text."""

    section_number: str
    title: str
    start: int  # start offset in the complete document stream
    end: int  # end offset in the complete document stream


@dataclass(frozen=True)
class RawSection:
    """A section detected in a PDF page's text, before any normalization."""

    section_number: str
    title: str
    body: str
    pages: tuple[int, ...]  # 1-indexed page numbers this section spans


def build_page_stream(page_texts: list[str]) -> str:
    """Join the text of all pages into a single string, with page breaks."""
    return _PAGE_SEPARATOR.join(page_texts)


def page_for_offset(stream: str, offset: int) -> int:
    """Given a character offset in the stream, return the 1-indexed page number."""
    if offset < 0 or offset > len(stream):
        raise ValueError(f"offset {offset} out of bounds for stream of length {len(stream)}")
    # Count the number of page separators before the offset
    return stream.count(_PAGE_SEPARATOR, 0, offset) + 1


def find_section_headings(stream: str, exclude_pages: set[int] = None) -> list[DetectedHeading]:
    """Find section headings in the text stream, returning their offsets."""
    exclude_pages = exclude_pages or set()
    headings: list[DetectedHeading] = []
    for match in SECTION_HEADING_RE.finditer(stream):
        line = match.group(0).strip()
        if _TOC_LEADER_RE.search(line):
            # Skip false positives that look like table-of-contents entries
            continue

        page_num = page_for_offset(stream, match.start())
        if page_num in exclude_pages:
            continue

        section_number = match.group(1).rstrip(".")
        title = match.group(2).strip()
        start = match.start()
        end = match.end() + 1
        # Skip false positives that look like footers
        if _FOOTER_FRAGMENT_RE.match(title):
            continue

        if _looks_like_prose_sentence(title):
            continue
        headings.append(DetectedHeading(section_number, title, start, end))
    return headings


def split_into_sections(stream: str, exclude_pages: set[int] = None) -> list[RawSection]:
    """Split the text stream into sections based on detected headings."""
    headings = find_section_headings(stream, exclude_pages)
    if not headings:
        return []

    sections: list[RawSection] = []
    for i, heading in enumerate(headings):
        start_offset = heading.end
        end_offset = headings[i + 1].start if i + 1 < len(headings) else len(stream)
        body = stream[start_offset:end_offset].replace(FORM_FEED, "\n").strip()

        start_page = page_for_offset(stream, heading.start)
        last_content_offset = max(start_offset, end_offset - 1)
        end_page = page_for_offset(stream, last_content_offset)
        pages = tuple(range(start_page, end_page + 1))

        sections.append(
            RawSection(
                section_number=heading.section_number,
                title=heading.title,
                body=body,
                pages=pages,
            )
        )
    return sections
