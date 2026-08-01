import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    PDF = "pdf"
    TXT = "txt"


class DocumentUpload(BaseModel):
    """Metadata describing an incoming document to be ingested."""

    filename: str = Field(..., min_length=1)
    document_type: DocumentType
    source_uri: str | None = Field(default=None, description="Optional origin URI or path")
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)


class DocumentChunk(BaseModel):
    """A single chunk of a parsed document, ready for embedding."""

    chunk_id: str
    document_id: str
    content: str = Field(..., min_length=1)
    chunk_index: int = Field(..., ge=0)
    start_offset: int = Field(..., ge=0)
    end_offset: int = Field(..., ge=0)
    metadata: dict = Field(default_factory=dict)


class ParsingResult(BaseModel):
    """Result of parsing + chunking a document."""

    document_id: str
    filename: str
    document_type: DocumentType
    total_pages: int | None = None
    total_chunks: int
    chunks: list[DocumentChunk]


class DocumentIngestSummary(BaseModel):
    """Per-document result returned by the upload endpoint."""

    document_id: uuid.UUID
    filename: str
    document_type: DocumentType
    total_pages: int | None = None
    total_chunks: int


class DocumentUploadResponse(BaseModel):
    """Response for a (possibly multi-file) document upload request."""

    workspace_id: uuid.UUID
    documents: list[DocumentIngestSummary]
