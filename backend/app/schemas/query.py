import uuid

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """Payload to run a retrieval-augmented, streamed query against a workspace."""

    workspace_id: uuid.UUID
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    # Module 10: omit (or send null) to start a fresh conversation - the
    # endpoint creates a ChatSession and returns its id on the `session`
    # and `done` SSE events so the client can continue the thread.
    session_id: uuid.UUID | None = Field(
        default=None, description="Existing chat session to continue; null starts a new one."
    )
