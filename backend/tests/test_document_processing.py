import pymupdf
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.document import DocumentType
from app.services.chunker import RecursiveChunker
from app.services.document_parser import DocumentParser


def make_pdf_bytes(pages_text: list[str]) -> bytes:
    """Builds an in-memory PDF with one page per string in `pages_text`."""
    doc = pymupdf.open()
    for text in pages_text:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    return doc.tobytes()


class TestHealthCheck:
    def test_health_check_returns_ok(self):
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert "app" in body


class TestDocumentParser:
    def test_parse_txt_document(self):
        parser = DocumentParser()
        content = b"Hello world, this is a plain text document."
        result = parser.parse("sample.txt", content)

        assert result.document_type == DocumentType.TXT
        assert result.total_pages is None
        assert result.total_chunks == len(result.chunks)
        assert result.chunks[0].content.startswith("Hello world")

    def test_parse_pdf_document(self):
        pdf_bytes = make_pdf_bytes(["Page one content.", "Page two content."])
        parser = DocumentParser()
        result = parser.parse("sample.pdf", pdf_bytes)

        assert result.document_type == DocumentType.PDF
        assert result.total_pages == 2
        assert result.total_chunks == len(result.chunks)
        combined = " ".join(c.content for c in result.chunks)
        assert "Page one content" in combined
        assert "Page two content" in combined

    def test_parse_unsupported_extension_raises(self):
        parser = DocumentParser()
        try:
            parser.parse("sample.docx", b"irrelevant")
            assert False, "Expected HTTPException for unsupported extension"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 400

    def test_parse_invalid_pdf_bytes_raises(self):
        parser = DocumentParser()
        try:
            parser.parse("broken.pdf", b"not a real pdf")
            assert False, "Expected HTTPException for invalid PDF content"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 422


class TestRecursiveChunker:
    def test_chunk_size_and_overlap_respected(self):
        chunk_size = 50
        chunk_overlap = 10
        chunker = RecursiveChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

        text = " ".join(f"word{i}" for i in range(200))
        chunks = chunker.chunk_text(text, document_id="doc-1")

        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk.content) <= chunk_size
            assert chunk.metadata["chunk_size"] == chunk_size
            assert chunk.metadata["chunk_overlap"] == chunk_overlap

    def test_chunks_are_sequentially_indexed_with_offsets(self):
        chunker = RecursiveChunker(chunk_size=40, chunk_overlap=5)
        text = "A" * 200
        chunks = chunker.chunk_text(text, document_id="doc-2")

        for expected_index, chunk in enumerate(chunks):
            assert chunk.chunk_index == expected_index
            assert chunk.document_id == "doc-2"
            assert chunk.end_offset >= chunk.start_offset

    def test_empty_text_returns_no_chunks(self):
        chunker = RecursiveChunker()
        assert chunker.chunk_text("   ", document_id="doc-3") == []

    def test_extra_metadata_is_merged(self):
        chunker = RecursiveChunker(chunk_size=100, chunk_overlap=20)
        chunks = chunker.chunk_text(
            "Some reasonably short text.",
            document_id="doc-4",
            extra_metadata={"filename": "foo.txt"},
        )
        assert chunks[0].metadata["filename"] == "foo.txt"

    def test_invalid_overlap_raises_value_error(self):
        try:
            RecursiveChunker(chunk_size=100, chunk_overlap=100)
            assert False, "Expected ValueError when overlap >= chunk_size"
        except ValueError:
            pass


class TestDocumentParserSecurity:
    """Strict security tests: malicious filenames must never be parsed successfully
    and must never fail silently — they must raise an explicit HTTPException."""

    def test_path_traversal_filename_is_rejected(self):
        parser = DocumentParser()
        malicious_filename = "../../../etc/passwd.pdf"
        pdf_bytes = make_pdf_bytes(["irrelevant"])

        with pytest.raises(HTTPException) as exc_info:
            parser.parse(malicious_filename, pdf_bytes)

        assert exc_info.value.status_code in (400, 422)
        assert exc_info.value.status_code == 400

    def test_absolute_path_filename_is_rejected(self):
        parser = DocumentParser()
        malicious_filename = "/etc/passwd.pdf"
        pdf_bytes = make_pdf_bytes(["irrelevant"])

        with pytest.raises(HTTPException) as exc_info:
            parser.parse(malicious_filename, pdf_bytes)

        assert exc_info.value.status_code == 400

    def test_windows_style_path_traversal_is_rejected(self):
        parser = DocumentParser()
        malicious_filename = "..\\..\\windows\\system32\\evil.pdf"
        pdf_bytes = make_pdf_bytes(["irrelevant"])

        with pytest.raises(HTTPException) as exc_info:
            parser.parse(malicious_filename, pdf_bytes)

        assert exc_info.value.status_code == 400

    def test_invalid_extension_payload_is_rejected(self):
        parser = DocumentParser()
        malicious_filename = "malicious_payload.exe"
        payload_bytes = b"MZ\x90\x00\x03\x00\x00\x00"  # PE executable magic bytes

        with pytest.raises(HTTPException) as exc_info:
            parser.parse(malicious_filename, payload_bytes)

        assert exc_info.value.status_code == 400

    def test_double_extension_payload_is_rejected(self):
        """Guards against disguised payloads like 'invoice.pdf.exe'."""
        parser = DocumentParser()
        malicious_filename = "invoice.pdf.exe"
        payload_bytes = b"MZ\x90\x00\x03\x00\x00\x00"

        with pytest.raises(HTTPException) as exc_info:
            parser.parse(malicious_filename, payload_bytes)

        assert exc_info.value.status_code == 400

    def test_rejected_filename_never_produces_a_result(self):
        """A rejected file must never silently degrade to a successful ParsingResult."""
        parser = DocumentParser()

        for malicious_filename in ("../../../etc/passwd.pdf", "malicious_payload.exe"):
            try:
                result = parser.parse(malicious_filename, b"anything")
            except HTTPException:
                continue
            assert False, f"Expected HTTPException for '{malicious_filename}', got result: {result}"
