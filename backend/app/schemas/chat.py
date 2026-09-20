import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, computed_field

from app.models.chat import ChatMessage, MessageRole
from app.services.nli_verifier import ClaimVerification, EntailmentLabel, aggregate_claims


class ClaimVerificationRead(BaseModel):
    """One claim's verdict, as stored and replayed."""

    claim: str
    label: EntailmentLabel
    score: float
    supporting_chunk_index: int | None = None


class ChatMessageRead(BaseModel):
    """One persisted turn, as replayed into the UI.

    `claims` is None on user turns and on assistant turns written before
    verdicts were stored. That is deliberately distinct from [], which means
    "verified, and nothing was flagged" - the UI renders those differently,
    and collapsing them would silently relabel unverified answers as clean.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: MessageRole
    content: str
    created_at: datetime
    claims: list[ClaimVerificationRead] | None = None

    @computed_field
    @property
    def overall_score(self) -> float | None:
        """Derived, never stored, so it cannot disagree with the claims."""
        return self._aggregate[0]

    @computed_field
    @property
    def is_fully_supported(self) -> bool | None:
        return self._aggregate[1]

    @property
    def _aggregate(self) -> tuple[float, bool] | tuple[None, None]:
        if self.claims is None:
            return None, None
        # Reuses the live verification formula rather than restating it, so a
        # replayed answer scores exactly as it did when first generated.
        score, supported = aggregate_claims(
            [
                ClaimVerification(
                    claim=c.claim,
                    label=c.label,
                    score=c.score,
                    supporting_chunk_index=c.supporting_chunk_index,
                )
                for c in self.claims
            ]
        )
        return score, supported

    @classmethod
    def from_model(cls, message: ChatMessage) -> "ChatMessageRead":
        return cls.model_validate(message)


class WorkspaceHistoryRead(BaseModel):
    """A workspace's most recent conversation.

    `session_id` is the load-bearing field, not the messages. Without it the
    client would render the restored turns and then open a *new* session on
    the next question, so the model would answer with no memory of anything
    on screen. Returning it lets the client continue the same thread.

    It is null when the workspace has never been used, in which case the
    client sends no session id and the backend creates one on first query.
    """

    workspace_id: uuid.UUID
    session_id: uuid.UUID | None
    messages: list[ChatMessageRead]
