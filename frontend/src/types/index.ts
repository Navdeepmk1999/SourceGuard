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
