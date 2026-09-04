"""Layout-aware document parsing (Module 11).

Extracts *structural elements* - headings, paragraphs, list items, tables -
rather than one flat text blob, so the downstream chunker can split on real
semantic boundaries instead of arbitrary character counts.

Why PyMuPDF rather than `unstructured`: PyMuPDF is already this project's
PDF dependency, and since 1.23 it ships `Page.find_tables()` plus full
per-span font metadata via `get_text("dict")` - enough for table extraction
and heading detection with **no new system packages**. `unstructured`'s
high-fidelity PDF partitioning wants poppler/tesseract and, for layout
detection, downloads ONNX/detectron model weights - which would contradict
two standing constraints in DESIGN.md: "No local model downloads" and the
deterministic, network-free offline dev/test path. Verified empirically on
this pinned PyMuPDF 1.28 before committing to the approach.
"""

import re
import statistics
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum

import fitz  # PyMuPDF
from fastapi import HTTPException

# A span is a heading if it is meaningfully larger than the document's body
# text. Relative, not absolute: font sizes vary wildly between documents, so
# a fixed "18pt is a heading" threshold would misclassify both a 10pt-body
# report and a large-print one.
_HEADING_SIZE_RATIO = 1.15
# A short, bold, non-sentence line reads as a heading even at body size -
# the common "**Section 1**" style that carries no size change.
_BOLD_HEADING_MAX_CHARS = 80
_BOLD_FLAG = 1 << 4  # PyMuPDF span flag bit for bold

_LIST_MARKER = re.compile(r"^\s*(?:[-*•·–—]|\(?\d+[.)]|[a-zA-Z][.)])\s+")
_MARKDOWN_HEADING = re.compile(r"^\s*#{1,6}\s+\S")
# A block whose overlap with a detected table covers more than this fraction
# of the block is treated as belonging to that table.
_TABLE_OVERLAP_THRESHOLD = 0.5


class ElementType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"


@dataclass
class LayoutElement:
    """One structural unit of a document."""

    element_type: ElementType
    text: str
    page_number: int | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def is_atomic(self) -> bool:
        """Elements that must never be split across chunks. A table split
        mid-rows loses the header row, and with it the column semantics that
        make the remaining cells interpretable."""
        return self.element_type is ElementType.TABLE


def _classify_text(text: str, size: float, body_size: float, is_bold: bool) -> ElementType:
    stripped = text.strip()
    if _LIST_MARKER.match(stripped):
        return ElementType.LIST_ITEM
    if _MARKDOWN_HEADING.match(stripped):
        return ElementType.HEADING
    if size > body_size * _HEADING_SIZE_RATIO:
        return ElementType.HEADING
    if is_bold and len(stripped) <= _BOLD_HEADING_MAX_CHARS and not stripped.endswith("."):
        return ElementType.HEADING
    return ElementType.PARAGRAPH


def _iter_spans(blocks: list[dict]) -> Iterator[dict]:
    for block in blocks:
        if block.get("type") != 0:  # 0 = text; 1 = image
            continue
        for line in block.get("lines", []):
            yield from line.get("spans", [])


def _body_font_size(doc: fitz.Document) -> float:
    """The document's dominant body font size, used as the heading baseline.

    Uses the median of all span sizes weighted by character count, so a
    handful of large title spans can't drag the baseline up and cause every
    paragraph to be misread as a heading.
    """
    sizes: list[float] = []
    for page in doc:
        for span in _iter_spans(page.get_text("dict").get("blocks", [])):
            text = span.get("text", "").strip()
            if text:
                # Weight by length: a 200-char paragraph should count for
                # more than a 3-char page number.
                sizes.extend([round(span.get("size", 0), 1)] * len(text))
    if not sizes:
        return 0.0
    return statistics.median(sizes)


def _block_belongs_to_table(block_rect: fitz.Rect, table_rects: list[fitz.Rect]) -> bool:
    """True when a text block sits inside a detected table.

    Without this, every cell's text would be emitted a second time as loose
    paragraphs alongside the rendered Markdown table - duplicating content
    and, worse, stripping it of the row/column structure the table form
    exists to preserve.
    """
    block_area = abs(block_rect)
    if block_area <= 0:
        return False
    for table_rect in table_rects:
        overlap = abs(block_rect & table_rect)
        if overlap / block_area > _TABLE_OVERLAP_THRESHOLD:
            return True
    return False


class LayoutParser:
    """Turns raw document bytes into ordered `LayoutElement`s."""

    def parse_pdf(self, content: bytes) -> tuple[list[LayoutElement], int]:
        """Returns (elements, page_count) for a PDF."""
        try:
            with fitz.open(stream=content, filetype="pdf") as doc:
                body_size = _body_font_size(doc)
                elements: list[LayoutElement] = []
                for page_index, page in enumerate(doc):
                    elements.extend(self._parse_page(page, page_index + 1, body_size))
                return elements, doc.page_count
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail=f"Failed to parse PDF document: {exc}"
            ) from exc

    def _parse_page(
        self, page: fitz.Page, page_number: int, body_size: float
    ) -> list[LayoutElement]:
        # Tables first: their bounding boxes determine which text blocks to
        # skip, so they must be known before any text is emitted.
        table_rects: list[fitz.Rect] = []
        # (vertical position, element) so tables and text can be re-ordered
        # into true reading order below.
        positioned: list[tuple[float, LayoutElement]] = []

        try:
            found = page.find_tables()
            tables = list(found.tables)
        except Exception:
            # Table detection is best-effort: a page whose vector graphics
            # confuse the finder should still yield its text, not 422.
            tables = []

        for table in tables:
            rect = fitz.Rect(table.bbox)
            table_rects.append(rect)
            try:
                markdown = table.to_markdown().strip()
            except Exception:
                markdown = ""
            if markdown:
                positioned.append(
                    (
                        rect.y0,
                        LayoutElement(
                            element_type=ElementType.TABLE,
                            text=markdown,
                            page_number=page_number,
                            metadata={"rows": len(table.extract())},
                        ),
                    )
                )

        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            block_rect = fitz.Rect(block["bbox"])
            if _block_belongs_to_table(block_rect, table_rects):
                continue

            # A block is a visual paragraph; classify it from its dominant
            # (largest) span so a paragraph containing one bold word isn't
            # mistaken for a heading.
            spans = [s for s in _iter_spans([block]) if s.get("text", "").strip()]
            if not spans:
                continue
            # Join a block's visual lines with "\n", not "" - consecutive
            # list items frequently land in one block, and concatenating
            # them bare produces "...growth- EMEA improved..." with the
            # items welded together and the bullet swallowed mid-word.
            lines = [
                "".join(s.get("text", "") for s in line.get("spans", [])).strip()
                for line in block.get("lines", [])
            ]
            text = "\n".join(line for line in lines if line).strip()
            if not text:
                continue

            lead = max(spans, key=lambda s: s.get("size", 0))
            element_type = _classify_text(
                text,
                size=lead.get("size", 0),
                body_size=body_size,
                is_bold=bool(int(lead.get("flags", 0)) & _BOLD_FLAG),
            )
            positioned.append(
                (
                    block_rect.y0,
                    LayoutElement(
                        element_type=element_type, text=text, page_number=page_number
                    ),
                )
            )

        positioned.sort(key=lambda pair: pair[0])
        return [element for _, element in positioned]

    def parse_text(self, text: str) -> list[LayoutElement]:
        """Returns elements for plain text.

        There is no font metadata to work with, so structure comes from the
        conventions plain text actually carries: blank lines separate
        paragraphs, `#` prefixes mark Markdown headings, and `-`/`1.`
        prefixes mark list items. Still a real improvement over splitting on
        a character count, which happily cuts mid-sentence.
        """
        elements: list[LayoutElement] = []
        for raw_block in re.split(r"\n\s*\n", text):
            block = raw_block.strip()
            if not block:
                continue
            if _MARKDOWN_HEADING.match(block):
                elements.append(
                    LayoutElement(element_type=ElementType.HEADING, text=block)
                )
                continue
            # A run of list lines is kept as one element: the items belong
            # together, and splitting them yields chunks with no context.
            if all(_LIST_MARKER.match(line) for line in block.splitlines() if line.strip()):
                elements.append(
                    LayoutElement(element_type=ElementType.LIST_ITEM, text=block)
                )
                continue
            elements.append(LayoutElement(element_type=ElementType.PARAGRAPH, text=block))
        return elements
