import uuid

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.schemas.document import DocumentChunk


class RecursiveChunker:
    """Wraps LangChain's recursive character splitter and tags chunks with
    offsets + metadata so downstream services can trace chunks back to source."""

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200) -> None:
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
        )

    def chunk_text(
        self,
        text: str,
        document_id: str,
        extra_metadata: dict | None = None,
    ) -> list[DocumentChunk]:
        """Split `text` into overlapping chunks and return them as DocumentChunk models."""
        if not text or not text.strip():
            return []

        raw_chunks = self._splitter.split_text(text)
        chunks: list[DocumentChunk] = []
        search_start = 0

        for index, raw_chunk in enumerate(raw_chunks):
            start_offset = text.find(raw_chunk, search_start)
            if start_offset == -1:
                start_offset = text.find(raw_chunk)
            end_offset = start_offset + len(raw_chunk)
            search_start = max(start_offset + 1, 0)

            metadata = {"chunk_size": self.chunk_size, "chunk_overlap": self.chunk_overlap}
            if extra_metadata:
                metadata.update(extra_metadata)

            chunks.append(
                DocumentChunk(
                    chunk_id=str(uuid.uuid4()),
                    document_id=document_id,
                    content=raw_chunk,
                    chunk_index=index,
                    start_offset=max(start_offset, 0),
                    end_offset=max(end_offset, 0),
                    metadata=metadata,
                )
            )

        return chunks
