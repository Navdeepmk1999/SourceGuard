import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class MessageRole(str, enum.Enum):
    """Who authored a chat message. Mirrors the OpenAI-compatible chat roles
    that `GenerationService` sends to Groq."""

    USER = "user"
    ASSISTANT = "assistant"


class ChatSession(Base):
    """One multi-turn conversation, scoped to a workspace and its owner."""

    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalized from the owning workspace so the RLS policy on this table
    # is a direct column comparison rather than a join back to `workspaces`
    # (same rationale as Workspace.user_id: no FK, since Supabase Auth owns
    # `auth.users` and this ORM doesn't model that schema).
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class ChatMessage(Base):
    """A single turn within a ChatSession."""

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    # native_enum=False -> VARCHAR + CHECK constraint rather than a Postgres
    # native ENUM type. Portable (identical DDL on Postgres and the SQLite
    # test database) and avoids a CREATE TYPE that the migration and
    # `Base.metadata.create_all` would each have to manage separately.
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, native_enum=False, length=16, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Python-side default with microsecond precision, NOT server_default=func.now().
    # Conversation ordering depends on this column, and SQLite's now() resolves
    # only to the second - sibling messages written in the same request would
    # tie and the replayed history could come back out of order. (The same
    # resolution limit already forced explicit timestamps in the workspace/
    # document ordering tests.)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    session: Mapped["ChatSession"] = relationship(back_populates="messages")
