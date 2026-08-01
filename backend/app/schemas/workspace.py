import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceCreate(BaseModel):
    """Payload to create a new research workspace."""

    name: str = Field(..., min_length=1, max_length=255)


class WorkspaceRead(BaseModel):
    """Workspace as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_at: datetime
