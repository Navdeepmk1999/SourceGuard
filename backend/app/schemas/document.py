import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class DocumentType(str, Enum):
    PDF = "pdf"
    TXT = "txt"


class DocumentStatus(str, Enum):
    """Lifecycle of an asynchronously ingested document.

    `pending` is the state the row is created in and returned as by the 202;
    it means accepted and queued, not yet touched.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        """True once a client can stop polling."""
        return self in (DocumentStatus.COMPLETED, DocumentStatus.FAILED)


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


class DocumentAccepted(BaseModel):
    """One document accepted for background ingestion."""

    document_id: uuid.UUID
    filename: str
    status: DocumentStatus = DocumentStatus.PENDING
    status_url: str


class DocumentUploadAccepted(BaseModel):
    """202 body: the work was queued, not done.

    Carries no chunk or page counts - nothing has been parsed yet. The client
    polls `status_url` per document until the status is terminal.
    """

    workspace_id: uuid.UUID
    documents: list[DocumentAccepted]


class DocumentStatusRead(BaseModel):
    """Polling response for a single document's ingestion progress."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    status: DocumentStatus
    chunk_count: int
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class DeletionResult(BaseModel):
    """What a DELETE actually removed.

    Counts are returned because cascade deletion is invisible otherwise: the
    caller asked to delete one workspace and silently destroyed every document
    and embedding beneath it.
    """

    id: uuid.UUID
    deleted_documents: int = 0
    deleted_chunks: int = 0


class DocumentRead(BaseModel):
    """A single document as returned by the workspace documents list endpoint.

    `total_chunks` is not a column on the `Document` model - it is computed
    (a per-document count of its `DocumentChunk` rows) by the endpoint's
    query, not read directly off an ORM attribute.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    document_type: DocumentType
    created_at: datetime
    total_chunks: int
    status: DocumentStatus = DocumentStatus.COMPLETED
