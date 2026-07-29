import uuid
from pathlib import Path

import fitz  # PyMuPDF
from fastapi import HTTPException

from app.schemas.document import DocumentType, ParsingResult
from app.services.chunker import RecursiveChunker

SUPPORTED_SUFFIXES = {".pdf": DocumentType.PDF, ".txt": DocumentType.TXT}


class DocumentParser:
    """Parses PDF and TXT documents into raw text, then delegates chunking
    to `RecursiveChunker` to produce a `ParsingResult`."""

    def __init__(self, chunker: RecursiveChunker | None = None) -> None:
        self._chunker = chunker or RecursiveChunker()

    def parse_pdf_bytes(self, content: bytes) -> tuple[str, int]:
        """Extract text from raw PDF bytes. Returns (full_text, page_count)."""
        try:
            with fitz.open(stream=content, filetype="pdf") as pdf:
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
            text, total_pages = self.parse_pdf_bytes(content)
        else:
            text = self.parse_txt_bytes(content)

        document_id = str(uuid.uuid4())
        chunks = self._chunker.chunk_text(
            text,
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
