import uuid

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """Payload to run a retrieval-augmented, streamed query against a workspace."""

    workspace_id: uuid.UUID
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
