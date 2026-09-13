-- 002_async_ingestion.sql
-- Adds ingestion status tracking to documents, and resizes the embedding
-- column for gemini-embedding-001. Idempotent; safe to re-run.
--
-- Run with an ADMIN connection (DDL). Apply AFTER 001.

BEGIN;

-- ---------------------------------------------------------------------------
-- Ingestion status
-- ---------------------------------------------------------------------------
-- Upload is now asynchronous: the row is created immediately at 'pending' and
-- the response returns 202 before any parsing happens. The row therefore has
-- to carry its own progress, because the HTTP request that created it is long
-- gone by the time ingestion finishes.
--
-- Deliberately a CHECK-constrained text column rather than a Postgres ENUM:
-- adding a value to an enum is a schema migration, and these states are the
-- kind of thing that grows.
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS error_message text,
  ADD COLUMN IF NOT EXISTS chunk_count integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'documents_status_check'
  ) THEN
    ALTER TABLE documents
      ADD CONSTRAINT documents_status_check
      CHECK (status IN ('pending', 'processing', 'completed', 'failed'));
  END IF;
END
$$;

-- Rows that predate this migration finished ingesting synchronously, so they
-- are complete by definition. Without this they would all read as 'pending'
-- and the UI would poll them forever.
UPDATE documents d
   SET status = 'completed',
       chunk_count = (SELECT count(*) FROM document_chunks c WHERE c.document_id = d.id)
 WHERE d.status = 'pending';

-- Status polling always filters by document id (primary key), but the
-- dashboard lists in-flight work per workspace.
CREATE INDEX IF NOT EXISTS idx_documents_workspace_status
  ON documents (workspace_id, status);

COMMIT;

-- ---------------------------------------------------------------------------
-- Embedding width  -- DESTRUCTIVE, run separately and deliberately
-- ---------------------------------------------------------------------------
-- pgvector fixes VECTOR(n) at DDL time and SQLAlchemy's create_all will not
-- alter an existing column, so switching embedding providers means dropping
-- the table. Chunks are derived data and can be rebuilt by re-uploading, but
-- the source documents cannot -- hence the TRUNCATE: leaving document rows
-- behind would show them in the sidebar as ingested while being unsearchable.
--
-- Uncomment only when EMBEDDING_DIMENSIONS in app/core/config.py has changed.
--
--   BEGIN;
--   DROP TABLE IF EXISTS document_chunks;
--   TRUNCATE TABLE documents;
--   COMMIT;
--
-- Then recreate the table, its RLS policy, and its grants:
--
--   python -m app.db.init_db
--
-- Verify:
--   SELECT format_type(atttypid, atttypmod) FROM pg_attribute
--    WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding';
