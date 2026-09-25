"""
Parses appendix E into forward and reverse relationship maps between record identifiers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.retrieval.extraction.section_detector import RawSection

_KB_RE = re.compile(r"^KB\d+$")
_INC_RE = re.compile(r"^INC\d+$")
_PRB_RE = re.compile(r"^PRB\d+$")
_KE_RE = re.compile(r"^KE\d+$")
_CHG_RE = re.compile(r"^CHG\d+$")
_MIR_RE = re.compile(r"^MIR\S+$")

_ROW_IDENTIFIER_RE = re.compile(r"\s*((?:KB|INC|PRB|KE|CHG|RITM)\d+|MIR-\d{4}-\d+)")
_TRAILING_SECTION_LIST_RE = re.compile(r"(\d+(?:\.\d+)*(?:\s*,\s*\d+(?:\.\d+)*)*)\s*$")

APPENDIX_E_TITLE_HINT = "identifier index"
_MAX_PLAUSIBLE_SECTION_NUMBER = 50


@dataclass(frozen=True)
class AppendixERelationships:
    """Relationships between record identifiers as parsed from Appendix E.
    Forward (identifier -> sections) and reverse (section -> identifiers) maps are provided.
    """

    forward: dict[str, list[str]] = field(default_factory=dict)
    reverse: dict[str, list[str]] = field(default_factory=dict)

    def sections_for(self, identifier: str) -> list[str]:
        """Return the list of section numbers associated with the given identifier."""
        return self.forward.get(identifier, [])

    def identifiers_for(self, section: str) -> list[str]:
        """Return the list of identifiers associated with the given section number."""
        return self.reverse.get(section, [])


@dataclass
class AppendixEReport:
    """Sanitized report of what the appendix parse actually found."""

    rows_parsed: int = 0
    warnings: list[str] = field(default_factory=list)


def find_appendix_e(sections: list[RawSection]) -> RawSection | None:
    """Locate the Appendix E section among already-detected raw sections."""
    for section in sections:
        if section.section_number.upper() == "E":
            return section
    for section in sections:
        if APPENDIX_E_TITLE_HINT in section.title.lower():
            return section
    return None


def _looks_like_section_number(token: str) -> bool:
    """
    If the token looks like a plausible section number (e.g., "1", "2.3", "4.5.6"), return True.
    """
    try:
        integer_part = int(token.split(".", 1)[0])
    except ValueError:
        return False
    return integer_part <= _MAX_PLAUSIBLE_SECTION_NUMBER


def _parse_row(line: str) -> tuple[str, list[str], list[str]] | None:
    """Parse one Appendix E row into (identifier, kept_sections, dropped_tokens).

    Returns None for a line with no leading identifier (the table header, a
    wrapped continuation line, or blank text) or with nothing after it.
    """
    id_match = _ROW_IDENTIFIER_RE.match(line)
    if not id_match:
        return None
    identifier = id_match.group(1)

    tail = line[id_match.end() :].strip()
    if not tail:
        return None

    trailing_match = _TRAILING_SECTION_LIST_RE.search(tail)
    if not trailing_match:
        return None

    tokens = [t.strip() for t in trailing_match.group(1).split(",")]
    kept: list[str] = []
    dropped: list[str] = []
    for token in tokens:
        if _looks_like_section_number(token):
            if token not in kept:
                kept.append(token)
        else:
            dropped.append(token)

    return identifier, kept, dropped


def parse_appendix_e_body(body: str) -> tuple[AppendixERelationships, AppendixEReport]:
    """Parse the body of Appendix E into forward and reverse maps of relationships."""
    forward: dict[str, list[str]] = {}
    reverse: dict[str, list[str]] = {}
    report = AppendixEReport()

    for line in body.splitlines():
        parsed = _parse_row(line)
        if parsed is None:
            continue

        identifier, kept_sections, dropped_tokens = parsed
        if not kept_sections and not dropped_tokens:
            continue

        report.rows_parsed += 1
        if dropped_tokens:
            report.warnings.append(
                f"row for {identifier} has unrecognized tokens: {', '.join(dropped_tokens)}"
            )

        if not kept_sections:
            continue

        forward.setdefault(identifier, [])
        for num in kept_sections:
            if num not in forward[identifier]:
                forward[identifier].append(num)
            reverse.setdefault(num, [])
            if identifier not in reverse[num]:
                reverse[num].append(identifier)

        return AppendixERelationships(forward=forward, reverse=reverse), report


def relationships_for_section(
    relationships: AppendixERelationships, section_number: str
) -> dict[str, tuple[str, ...]]:
    """Split a section's related identifiers into the six typed tuples that
    ManualSectionChunk carries.
    """
    identifiers = relationships.identifiers_for(section_number)
    return {
        "related_article_ids": tuple(i for i in identifiers if _KB_RE.match(i)),
        "related_incident_ids": tuple(i for i in identifiers if _INC_RE.match(i)),
        "related_problem_ids": tuple(i for i in identifiers if _PRB_RE.match(i)),
        "related_known_error_ids": tuple(i for i in identifiers if _KE_RE.match(i)),
        "related_change_ids": tuple(i for i in identifiers if _CHG_RE.match(i)),
        "related_mir_ids": tuple(i for i in identifiers if _MIR_RE.match(i)),
    }
