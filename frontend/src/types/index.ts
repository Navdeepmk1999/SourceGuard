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
