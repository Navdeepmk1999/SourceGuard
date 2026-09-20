"""Conversation memory: chat sessions and the sliding-window message history
injected into each generation prompt (Module 10).

Kept out of the endpoint (per the project's "no monolithic files" rule) so
the session/history logic is unit-testable without going through SSE.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatMessage, ChatSession, MessageRole

# Sliding-window size: how many prior messages are replayed into the prompt.
# Caps prompt growth (and therefore per-request token cost and latency) on a
# long-running conversation - an unbounded history would grow every turn
# until it blew the model's context window.
HISTORY_WINDOW_SIZE = 10


async def get_or_create_session(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    session_id: uuid.UUID | None,
) -> ChatSession:
    """Resolves `session_id` to an existing ChatSession, or creates a new one.

    Raises `LookupError` if `session_id` is given but doesn't exist, belongs
    to another user, or belongs to a different workspace - the caller
    translates that into a 404. Ownership is re-checked here in application
    code rather than relying on the `chat_session_isolation` RLS policy
    alone, for the same reason as `ensure_workspace_owner`: RLS is inert on
    the SQLite test database and skippable by a BYPASSRLS role.
    """
    if session_id is not None:
        chat_session = await session.get(ChatSession, session_id)
        if (
            chat_session is None
            or chat_session.user_id != user_id
            or chat_session.workspace_id != workspace_id
        ):
            raise LookupError("Chat session not found")
        return chat_session

    chat_session = ChatSession(workspace_id=workspace_id, user_id=user_id)
    session.add(chat_session)
    await session.commit()
    await session.refresh(chat_session)
    return chat_session


async def load_recent_messages(
    session: AsyncSession, session_id: uuid.UUID, limit: int = HISTORY_WINDOW_SIZE
) -> list[ChatMessage]:
    """Returns the most recent `limit` messages in chronological order.

    Fetched newest-first with LIMIT (so the database does the windowing
    rather than loading an entire long conversation into memory), then
    reversed - the model needs oldest-to-newest to read the exchange in
    order.
    """
    result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))


def to_prompt_history(messages: list[ChatMessage]) -> list[dict[str, str]]:
    """Converts ORM rows into the OpenAI-compatible `{role, content}` dicts
    that `GenerationService.stream_answer` splices into its messages array."""
    return [{"role": m.role.value, "content": m.content} for m in messages]


async def save_message(
    session: AsyncSession,
    session_id: uuid.UUID,
    role: MessageRole,
    content: str,
    claims: list[dict] | None = None,
) -> ChatMessage:
    """Persists one turn and commits.

    `claims` stays None for user turns and for assistant turns whose
    verification has not run yet - see `attach_claims`, which fills it in
    afterwards.
    """
    message = ChatMessage(session_id=session_id, role=role, content=content, claims=claims)
    session.add(message)
    await session.commit()
    return message


async def attach_claims(
    session: AsyncSession, message: ChatMessage, claims: list[dict]
) -> None:
    """Records verification verdicts against an already-saved turn.

    A second write, deliberately. The answer is persisted the moment streaming
    ends, before verification runs, so a failure in the verifier costs the
    audit trail for that turn rather than the answer itself. Folding both into
    one write would trade a recoverable gap for a lost response.
    """
    message.claims = claims
    session.add(message)
    await session.commit()


async def get_latest_session(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> ChatSession | None:
    """The workspace's most recently created session, or None if never used.

    A workspace accumulates one session per "new conversation", so restoring
    the UI means picking the newest rather than merging them - concatenating
    every session would replay unrelated conversations as one thread.

    Scoped by `user_id` as well as workspace: RLS covers this on Postgres, but
    the application check is what makes the behaviour identical on the SQLite
    test database, where RLS does not exist.
    """
    result = await session.execute(
        select(ChatSession)
        .where(ChatSession.workspace_id == workspace_id, ChatSession.user_id == user_id)
        .order_by(ChatSession.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
