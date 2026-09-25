"""Public exports for the manual-section retrieval subpackage.

Everything a caller needs to parse, chunk, ingest, and search the manual
lives behind this package; internals (regexes, per-page classification
dataclasses) stay in their own modules.
"""

from app.retrieval.extraction.parse_appendix import (
    AppendixERelationships,
    AppendixEReport,
    parse_appendix_e_body,
    relationships_for_section,
)
from app.retrieval.extraction.section_detector import RawSection, split_into_sections
from app.retrieval.manual.manual_chunking import chunk_section, chunk_sections
from app.retrieval.manual.manual_ingest import (
    DEFAULT_MANUAL_COLLECTION_NAME,
    ingest_manual_sections,
)
from app.retrieval.manual.manual_parser import ParseReport, parse_manual
from app.retrieval.manual.manual_sources import (
    ManualCorpusJSONSource,
    ManualSource,
    sections_and_relationships_to_json,
)

__all__ = [
    "AppendixEReport",
    "AppendixERelationships",
    "DEFAULT_MANUAL_COLLECTION_NAME",
    "ManualCorpusJSONSource",
    "ManualSearchResult",
    "ManualSectionHit",
    "ManualSource",
    "ParseReport",
    "RawSection",
    "chunk_section",
    "chunk_sections",
    "ingest_manual_sections",
    "parse_appendix_e_body",
    "parse_manual",
    "relationships_for_section",
    "search_manual_sections",
    "sections_and_relationships_to_json",
    "split_into_sections",
    "timed_search_manual_sections",
]
