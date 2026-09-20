// Mirrors backend/app/models/*.py (SQLAlchemy models) and
// backend/app/services/nli_verifier.py (VerificationResult / ClaimVerification).

export interface Workspace {
  id: string;
  name: string;
  created_at: string;
}

export type DocumentType = "pdf" | "txt";

export interface Document {
  id: string;
  workspace_id: string;
  filename: string;
  document_type: DocumentType;
  created_at: string;
}

export interface DocumentChunk {
  id: string;
  document_id: string;
  content: string;
  chunk_index: number;
  metadata: Record<string, unknown>;
  created_at: string;
}

// Mirrors backend/app/schemas/document.py::DocumentIngestSummary / DocumentUploadResponse.
export interface DocumentIngestSummary {
  document_id: string;
  filename: string;
  document_type: DocumentType;
  total_pages: number | null;
  total_chunks: number;
}

export interface DocumentUploadResponse {
  workspace_id: string;
  documents: DocumentIngestSummary[];
}

// Mirrors backend/app/schemas/document.py::DocumentRead. Not the same shape as
// `Document` above (no `workspace_id` - already scoped by the request URL -
// plus a computed `total_chunks` the ORM model doesn't carry as a column).
export interface WorkspaceDocument {
  id: string;
  filename: string;
  document_type: DocumentType;
  created_at: string;
  total_chunks: number;
}

export type EntailmentLabel =
  | "entailed"
  | "not_entailed"
  | "insufficient_evidence";

export interface ClaimVerification {
  claim: string;
  label: EntailmentLabel;
  score: number;
  supporting_chunk_index: number | null;
}

export interface VerificationResult {
  claims: ClaimVerification[];
  overall_score: number;
  is_fully_supported: boolean;
}

/** Ingestion lifecycle. `completed` and `failed` are terminal — stop polling. */
export type DocumentStatus = "pending" | "processing" | "completed" | "failed";

export const TERMINAL_STATUSES: readonly DocumentStatus[] = ["completed", "failed"];

/** One file accepted for background ingestion. Carries no counts — nothing is parsed yet. */
export interface DocumentAccepted {
  document_id: string;
  filename: string;
  status: DocumentStatus;
  status_url: string;
}

/** 202 body from `POST /documents/upload`: queued, not done. */
export interface DocumentUploadAccepted {
  workspace_id: string;
  documents: DocumentAccepted[];
}

/** Polling response from `GET /documents/{id}/status`. */
export interface DocumentStatusRead {
  id: string;
  filename: string;
  status: DocumentStatus;
  chunk_count: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

/** What a DELETE removed. Counts make cascade deletion visible to the user. */
export interface DeletionResult {
  id: string;
  deleted_documents: number;
  deleted_chunks: number;
}

export type ChatRole = "user" | "assistant";

/** One persisted turn replayed from the backend. */
export interface ChatMessageRead {
  id: string;
  role: ChatRole;
  content: string;
  created_at: string;
  /**
   * Stored verification verdicts.
   *
   * `null` means none were stored - a user turn, or an assistant turn from
   * before verdicts were persisted. That is deliberately distinct from `[]`,
   * which means "verified, and nothing was flagged". Rendering them the same
   * way would relabel an unverified answer as clean.
   */
  claims: ClaimVerification[] | null;
  /** Derived server-side from `claims`; null whenever `claims` is null. */
  overall_score: number | null;
  is_fully_supported: boolean | null;
}

/**
 * A workspace's most recent conversation.
 *
 * `session_id` matters as much as the messages: it is what lets the next
 * question continue the same thread. Without sending it back, the backend
 * opens a new session and answers with no memory of what is on screen.
 * Null when the workspace has never been used.
 */
export interface WorkspaceHistory {
  workspace_id: string;
  session_id: string | null;
  messages: ChatMessageRead[];
}
