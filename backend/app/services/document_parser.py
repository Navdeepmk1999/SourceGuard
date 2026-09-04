import uuid
from pathlib import Path

import pymupdf
from fastapi import HTTPException

from app.schemas.document import DocumentType, ParsingResult
from app.services.chunker import RecursiveChunker
from app.services.layout_parser import LayoutParser
from app.services.semantic_chunker import SemanticChunker

SUPPORTED_SUFFIXES = {".pdf": DocumentType.PDF, ".txt": DocumentType.TXT}


class DocumentParser:
    """Parses PDF and TXT documents into structural elements (headings,
    paragraphs, lists, tables) and chunks them on semantic boundaries to
    produce a `ParsingResult`.

    Module 11 replaced the flat text -> `RecursiveCharacterTextSplitter`
    path with `LayoutParser` -> `SemanticChunker`. `parse_pdf_bytes` /
    `parse_txt_bytes` are retained: they remain the raw-text accessors and
    keep the 422 decode/parse error contract that the upload endpoint and
    its regression tests depend on.
    """

    def __init__(
        self,
        chunker: RecursiveChunker | None = None,
        layout_parser: LayoutParser | None = None,
        semantic_chunker: SemanticChunker | None = None,
    ) -> None:
        self._chunker = chunker or RecursiveChunker()
        self._layout_parser = layout_parser or LayoutParser()
        # Inherits the ceiling from whatever RecursiveChunker was configured
        # with, so `DocumentParser(RecursiveChunker(chunk_size=...))` keeps
        # controlling chunk size as it did before Module 11.
        self._semantic_chunker = semantic_chunker or SemanticChunker(
            max_chunk_size=self._chunker.chunk_size
        )

    def parse_pdf_bytes(self, content: bytes) -> tuple[str, int]:
        """Extract text from raw PDF bytes. Returns (full_text, page_count)."""
        try:
            with pymupdf.open(stream=content, filetype="pdf") as pdf:
                pages = [page.get_text() for page in pdf]
                return "\n".join(pages), len(pages)
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail=f"Failed to parse PDF document: {exc}"
            ) from exc

    def parse_txt_bytes(self, content: bytes) -> str:
        """Decode raw TXT bytes into text."""
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=422, detail=f"Failed to decode text document: {exc}"
            ) from exc

    def _validate_filename(self, filename: str) -> str:
        """Reject path traversal / directory components. Returns the bare filename."""
        if not filename or filename != Path(filename).name or ".." in filename:
            raise HTTPException(
                status_code=400,
                detail="Invalid filename: path traversal or directory components are not allowed",
            )
        return filename

    def parse(self, filename: str, content: bytes) -> ParsingResult:
        """Parse a document (by filename extension) and chunk it into a ParsingResult."""
        filename = self._validate_filename(filename)

        suffix = Path(filename).suffix.lower()
        document_type = SUPPORTED_SUFFIXES.get(suffix)
        if document_type is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported document type '{suffix}'. Supported: {list(SUPPORTED_SUFFIXES)}",
            )

        total_pages: int | None = None
        if document_type == DocumentType.PDF:
            elements, total_pages = self._layout_parser.parse_pdf(content)
        else:
            # Decoded via parse_txt_bytes so the 422 contract for undecodable
            # bytes is unchanged.
            elements = self._layout_parser.parse_text(self.parse_txt_bytes(content))

        document_id = str(uuid.uuid4())
        chunks = self._semantic_chunker.chunk_elements(
            elements,
            document_id=document_id,
            extra_metadata={"filename": filename, "document_type": document_type.value},
        )

        return ParsingResult(
            document_id=document_id,
            filename=filename,
            document_type=document_type,
            total_pages=total_pages,
            total_chunks=len(chunks),
            chunks=chunks,
        )
