from __future__ import annotations

from app.models.manual_section import ManualSection, ManualSectionType
from app.retrieval.extraction.parse_appendix import AppendixERelationships
from app.retrieval.manual.manual_chunking import chunk_section, chunk_sections


def _section(
    section_id="section-1.3",
    section_number="1.3",
    title="Conventions",
    body="Short body text.",
    pages=(7,),
):
    return ManualSection(
        section_id=section_id,
        section_number=section_number,
        title=title,
        body=body,
        content_type=ManualSectionType.PROSE,
        pages=pages,
    )


class TestChunkSection:
    def test_short_body_produces_a_single_chunk(self):
        section = _section()
        chunks = chunk_section(section, AppendixERelationships())
        assert len(chunks) == 1
        assert chunks[0].text == "Short body text."
        assert chunks[0].chunk_index == 0
        assert chunks[0].total_chunks == 1
        assert chunks[0].section_id == "section-1.3"

    def test_empty_body_returns_no_chunks(self):
        section = _section(body="   ")
        assert chunk_section(section, AppendixERelationships()) == []

    def test_long_body_splits_into_sequential_chunks(self):
        paragraph = "This is a sentence about incident handling procedures. " * 20
        section = _section(
            section_id="section-6.4",
            section_number="6.4",
            title="KB0001",
            body=paragraph,
            pages=(18, 19),
        )
        chunks = chunk_section(section, AppendixERelationships(), chunk_size=200, chunk_overlap=20)

        assert len(chunks) > 1
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
        assert all(c.total_chunks == len(chunks) for c in chunks)
        assert all(c.section_number == "6.4" for c in chunks)
        assert all(c.pages == (18, 19) for c in chunks)
        # No chunk should be empty or pure whitespace.
        assert all(c.text.strip() for c in chunks)

    def test_chunk_ids_are_unique_within_a_section(self):
        paragraph = "This is a sentence about incident handling procedures. " * 20
        section = _section(body=paragraph)
        chunks = chunk_section(section, AppendixERelationships(), chunk_size=200, chunk_overlap=20)
        assert len({c.chunk_id for c in chunks}) == len(chunks)

    def test_relationships_for_the_section_are_attached_to_every_chunk(self):
        section = _section(section_id="section-6.13", section_number="6.13", title="KB0010")
        relationships = AppendixERelationships(
            forward={}, reverse={"6.13": ["KB0010", "MIR-2026-03"]}
        )
        chunks = chunk_section(section, relationships)
        assert chunks[0].related_article_ids == ("KB0010",)
        assert chunks[0].related_mir_ids == ("MIR-2026-03",)

    def test_section_with_no_related_identifiers_gets_empty_tuples(self):
        section = _section(section_number="99.9")
        chunks = chunk_section(section, AppendixERelationships())
        assert chunks[0].related_article_ids == ()


class TestChunkSections:
    def test_flattens_chunks_across_multiple_sections(self):
        s1 = _section(section_id="section-1", section_number="1", title="A", body="Body one.")
        s2 = _section(section_id="section-2", section_number="2", title="B", body="Body two.")
        chunks = chunk_sections([s1, s2], AppendixERelationships())
        assert len(chunks) == 2
        assert {c.section_id for c in chunks} == {"section-1", "section-2"}

    def test_chunk_ids_are_unique_across_sections(self):
        s1 = _section(section_id="section-1", section_number="1", body="Body one.")
        s2 = _section(section_id="section-2", section_number="2", body="Body two.")
        chunks = chunk_sections([s1, s2], AppendixERelationships())
        assert len({c.chunk_id for c in chunks}) == len(chunks)

    def test_sections_with_empty_body_are_skipped_without_error(self):
        empty = _section(section_id="section-e", section_number="e", title="Empty", body="")
        real = _section(section_id="section-r", section_number="r", title="Real", body="Real text.")
        chunks = chunk_sections([empty, real], AppendixERelationships())
        assert len(chunks) == 1
        assert chunks[0].section_id == "section-r"

    def test_empty_section_list_returns_empty_list(self):
        assert chunk_sections([], AppendixERelationships()) == []
