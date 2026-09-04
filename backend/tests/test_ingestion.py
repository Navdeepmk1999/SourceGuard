"""Module 11: layout-aware parsing + semantic boundary enforcement."""

import fitz
import pytest

from app.services.document_parser import DocumentParser
from app.services.layout_parser import ElementType, LayoutElement, LayoutParser
from app.services.semantic_chunker import SemanticChunker


def make_structured_pdf() -> bytes:
    """A PDF with a title, a section heading, a paragraph, a two-item list,
    and a ruled 3x3 table - i.e. every element type the parser classifies."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 80), "Quarterly Revenue Report", fontsize=18, fontname="hebo")
    page.insert_text((72, 115), "Section 1: Regional Overview", fontsize=13, fontname="hebo")
    page.insert_textbox(
        fitz.Rect(72, 130, 500, 180),
        "Revenue grew across all regions this quarter. "
        "The table below breaks out figures by region.",
        fontsize=10,
    )
    page.insert_text((72, 195), "- North America led growth", fontsize=10)
    page.insert_text((72, 210), "- EMEA improved margins", fontsize=10)

    cells = [
        ["Region", "Revenue", "Growth"],
        ["NA", "$4.2M", "12%"],
        ["EMEA", "$3.1M", "8%"],
    ]
    x0, y0, col_w, row_h = 72, 235, 110, 20
    for r in range(3):
        for c in range(3):
            rect = fitz.Rect(x0 + c * col_w, y0 + r * row_h, x0 + (c + 1) * col_w, y0 + (r + 1) * row_h)
            page.draw_rect(rect, color=(0, 0, 0), width=0.7)
            page.insert_text((rect.x0 + 4, rect.y0 + 14), cells[r][c], fontsize=9)

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


class TestLayoutParsing:
    def test_pdf_elements_are_classified_by_structure(self):
        elements, pages = LayoutParser().parse_pdf(make_structured_pdf())

        assert pages == 1
        types = {e.element_type for e in elements}
        assert ElementType.HEADING in types
        assert ElementType.PARAGRAPH in types
        assert ElementType.TABLE in types

    def test_larger_font_is_detected_as_a_heading(self):
        elements, _ = LayoutParser().parse_pdf(make_structured_pdf())
        headings = [e.text for e in elements if e.element_type is ElementType.HEADING]

        assert any("Quarterly Revenue Report" in h for h in headings)
        assert any("Section 1" in h for h in headings)

    def test_table_is_extracted_as_markdown(self):
        elements, _ = LayoutParser().parse_pdf(make_structured_pdf())
        tables = [e for e in elements if e.element_type is ElementType.TABLE]

        assert len(tables) == 1
        markdown = tables[0].text
        # Row/column relationships must survive into the embedded text.
        assert "|Region|Revenue|Growth|" in markdown
        assert "|NA|$4.2M|12%|" in markdown
        assert "|EMEA|$3.1M|8%|" in markdown

    def test_table_cell_text_is_not_duplicated_as_paragraphs(self):
        """The cells sit inside the page's text layer too. Without bbox
        suppression they'd be emitted a second time as loose prose -
        duplicating content and stripping its column structure."""
        elements, _ = LayoutParser().parse_pdf(make_structured_pdf())
        non_table_text = " ".join(
            e.text for e in elements if e.element_type is not ElementType.TABLE
        )

        assert "$4.2M" not in non_table_text
        assert "Growth" not in non_table_text

    def test_list_items_in_one_block_are_not_welded_together(self):
        """PyMuPDF often puts consecutive list lines in a single block;
        joining its lines without a separator produced '...growth- EMEA...'."""
        elements, _ = LayoutParser().parse_pdf(make_structured_pdf())
        all_text = "\n".join(e.text for e in elements)

        assert "growth- EMEA" not in all_text
        assert "- North America led growth" in all_text
        assert "- EMEA improved margins" in all_text

    def test_invalid_pdf_raises_422(self):
        with pytest.raises(Exception) as exc_info:
            LayoutParser().parse_pdf(b"not a real pdf")
        assert getattr(exc_info.value, "status_code", None) == 422


class TestPlainTextLayout:
    def test_markdown_headings_and_lists_are_classified(self):
        text = (
            "# Overview\n\n"
            "This is the first paragraph of the document.\n\n"
            "- first item\n- second item\n\n"
            "A closing paragraph."
        )
        elements = LayoutParser().parse_text(text)
        by_type = {e.element_type for e in elements}

        assert ElementType.HEADING in by_type
        assert ElementType.LIST_ITEM in by_type
        assert ElementType.PARAGRAPH in by_type

    def test_list_run_is_kept_as_one_element(self):
        elements = LayoutParser().parse_text("- alpha\n- beta\n- gamma")

        assert len(elements) == 1
        assert elements[0].element_type is ElementType.LIST_ITEM
        assert "alpha" in elements[0].text and "gamma" in elements[0].text

    def test_blank_lines_separate_paragraphs(self):
        elements = LayoutParser().parse_text("First para.\n\nSecond para.")
        assert [e.text for e in elements] == ["First para.", "Second para."]


class TestSemanticBoundaries:
    def test_table_is_never_merged_with_surrounding_prose(self):
        elements = [
            LayoutElement(ElementType.PARAGRAPH, "Short intro."),
            LayoutElement(ElementType.TABLE, "|a|b|\n|---|---|\n|1|2|"),
            LayoutElement(ElementType.PARAGRAPH, "Short outro."),
        ]
        chunks = SemanticChunker(max_chunk_size=1000).chunk_elements(elements, "doc-1")

        table_chunks = [c for c in chunks if c.metadata["contains_table"]]
        assert len(table_chunks) == 1
        # Atomic: the table chunk carries the table and nothing else.
        assert "Short intro." not in table_chunks[0].content
        assert "Short outro." not in table_chunks[0].content
        assert "|1|2|" in table_chunks[0].content

    def test_table_is_never_split_even_when_over_the_ceiling(self):
        big_table = "|col_a|col_b|\n|---|---|\n" + "\n".join(
            f"|value_{i}|other_{i}|" for i in range(80)
        )
        elements = [LayoutElement(ElementType.TABLE, big_table)]
        chunks = SemanticChunker(max_chunk_size=200).chunk_elements(elements, "doc-2")

        assert len(chunks) == 1, "an atomic table must not be split by the size ceiling"
        assert chunks[0].content.count("|value_") == 80

    def test_heading_binds_to_the_content_it_introduces(self):
        elements = [
            LayoutElement(ElementType.HEADING, "Section A"),
            LayoutElement(ElementType.PARAGRAPH, "Body of section A."),
        ]
        chunks = SemanticChunker(max_chunk_size=1000).chunk_elements(elements, "doc-3")

        assert len(chunks) == 1
        assert chunks[0].content.startswith("Section A")
        assert "Body of section A." in chunks[0].content
        assert chunks[0].metadata["section"] == "Section A"

    def test_a_new_heading_starts_a_new_chunk(self):
        elements = [
            LayoutElement(ElementType.HEADING, "Section A"),
            LayoutElement(ElementType.PARAGRAPH, "Body A."),
            LayoutElement(ElementType.HEADING, "Section B"),
            LayoutElement(ElementType.PARAGRAPH, "Body B."),
        ]
        chunks = SemanticChunker(max_chunk_size=1000).chunk_elements(elements, "doc-4")

        assert len(chunks) == 2
        assert chunks[0].metadata["section"] == "Section A"
        assert chunks[1].metadata["section"] == "Section B"
        assert "Body B." not in chunks[0].content

    def test_continuation_chunks_are_prefixed_with_their_section_heading(self):
        """A chunk retrieved on its own must still say what section it's from."""
        elements = [LayoutElement(ElementType.HEADING, "Section A")] + [
            LayoutElement(ElementType.PARAGRAPH, f"Paragraph number {i} with filler text.")
            for i in range(8)
        ]
        chunks = SemanticChunker(max_chunk_size=120).chunk_elements(elements, "doc-5")

        assert len(chunks) > 1
        continuations = [c for c in chunks if c.metadata["heading_prefixed"]]
        assert continuations, "expected at least one continuation chunk"
        for chunk in continuations:
            assert chunk.content.startswith("Section A")

    def test_splits_land_on_element_boundaries_not_mid_sentence(self):
        elements = [
            LayoutElement(ElementType.PARAGRAPH, f"Sentence {i} ends here.") for i in range(10)
        ]
        chunks = SemanticChunker(max_chunk_size=80).chunk_elements(elements, "doc-6")

        assert len(chunks) > 1
        for chunk in chunks:
            # Every chunk is whole elements joined - so it ends where an
            # element ends, never part-way through one.
            assert chunk.content.endswith("ends here.")

    def test_oversized_single_element_falls_back_and_is_flagged(self):
        huge = "word " * 500
        elements = [LayoutElement(ElementType.PARAGRAPH, huge)]
        chunks = SemanticChunker(max_chunk_size=200).chunk_elements(elements, "doc-7")

        assert len(chunks) > 1
        assert all(c.metadata.get("oversized_element_split") for c in chunks)

    def test_chunks_are_sequentially_indexed_with_ordered_offsets(self):
        elements = [
            LayoutElement(ElementType.PARAGRAPH, f"Paragraph {i} body text here.")
            for i in range(6)
        ]
        chunks = SemanticChunker(max_chunk_size=60).chunk_elements(elements, "doc-8")

        for expected_index, chunk in enumerate(chunks):
            assert chunk.chunk_index == expected_index
            assert chunk.end_offset >= chunk.start_offset
            assert chunk.document_id == "doc-8"

    def test_offsets_map_back_to_the_reconstructed_source(self):
        """Offset traceability (DESIGN.md Module 1) must survive the switch
        from character splitting to element assembly."""
        elements = [
            LayoutElement(ElementType.PARAGRAPH, "Alpha paragraph."),
            LayoutElement(ElementType.PARAGRAPH, "Beta paragraph."),
        ]
        chunker = SemanticChunker(max_chunk_size=20)
        chunks = chunker.chunk_elements(elements, "doc-9")
        source = "\n\n".join(e.text for e in elements)

        for chunk in chunks:
            if not chunk.metadata["heading_prefixed"]:
                assert source[chunk.start_offset : chunk.end_offset] == chunk.content

    def test_empty_elements_return_no_chunks(self):
        assert SemanticChunker().chunk_elements([], "doc-10") == []

    def test_invalid_max_chunk_size_raises(self):
        with pytest.raises(ValueError):
            SemanticChunker(max_chunk_size=0)


class TestDocumentParserIntegration:
    def test_pdf_ingestion_produces_semantic_chunks_with_intact_table(self):
        result = DocumentParser().parse("report.pdf", make_structured_pdf())

        assert result.total_chunks == len(result.chunks)
        assert all(c.metadata["strategy"] == "semantic_layout" for c in result.chunks)

        table_chunks = [c for c in result.chunks if c.metadata["contains_table"]]
        assert len(table_chunks) == 1
        assert "|NA|$4.2M|12%|" in table_chunks[0].content

    def test_pdf_chunks_carry_page_and_filename_metadata(self):
        result = DocumentParser().parse("report.pdf", make_structured_pdf())

        for chunk in result.chunks:
            assert chunk.metadata["filename"] == "report.pdf"
            assert chunk.metadata["document_type"] == "pdf"
            assert chunk.metadata["pages"] == [1]

    def test_txt_ingestion_uses_semantic_boundaries(self):
        text = b"# Title\n\nFirst paragraph here.\n\n- item one\n- item two"
        result = DocumentParser().parse("notes.txt", text)

        assert result.total_pages is None
        combined = "\n".join(c.content for c in result.chunks)
        assert "First paragraph here." in combined
        assert "- item one" in combined
