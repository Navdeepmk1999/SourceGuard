-- 001_row_level_security.sql
-- Tenant isolation for SourceGuard. Idempotent; safe to re-run.
--
-- Run in the Supabase SQL editor, or via psql with an ADMIN connection.
--
-- ============================================================================
-- READ THIS FIRST: these policies do NOT use auth.uid()
-- ============================================================================
-- Supabase's documented RLS pattern is `auth.uid() = user_id`. That works only
-- when the caller reaches Postgres through PostgREST/supabase-js, which puts
-- the end user's JWT on the database session.
--
-- SourceGuard's backend does not. FastAPI verifies the Supabase JWT itself
-- (ES256 via JWKS) and then connects to Postgres as one long-lived role,
-- publishing the tenant on the session with:
--
--     SELECT set_config('app.current_user_id', '<uuid>', false);
--
-- so auth.uid() is NULL on every one of those connections and an auth.uid()
-- policy would deny all rows. The policies below read the session variable
-- instead. They also fall back to auth.uid(), so direct supabase-js access
-- keeps working if the frontend ever queries these tables itself.
--
-- ============================================================================
-- WHY THE ROLE MATTERS MORE THAN THE POLICIES
-- ============================================================================
-- PostgreSQL exempts SUPERUSER and BYPASSRLS roles from every policy,
-- unconditionally. No table-level setting overrides this -- FORCE ROW LEVEL
-- SECURITY extends policies to the table OWNER, not to a superuser.
--
-- So these policies enforce NOTHING unless the application's DATABASE_URL
-- points at a restricted role. Verify after deploying:
--
--     SELECT current_user, rolsuper, rolbypassrls
--       FROM pg_roles WHERE rolname = current_user;
--     -- rolsuper and rolbypassrls must BOTH be false
-- ============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

-- Resolves the active tenant, preferring the backend's session variable and
-- falling back to Supabase's JWT claim.
--
-- NULLIF is load-bearing: current_setting(..., true) returns NULL when never
-- set but an EMPTY STRING once cleared, and ''::uuid raises. Without NULLIF a
-- cleared context turns every query into an error instead of a clean zero-row
-- result -- it must fail closed, not fail loudly.
CREATE OR REPLACE FUNCTION app_current_tenant()
RETURNS uuid
LANGUAGE sql
STABLE
AS $$
  SELECT COALESCE(
    NULLIF(current_setting('app.current_user_id', true), '')::uuid,
    auth.uid()
  );
$$;

-- ---------------------------------------------------------------------------
-- workspaces -- the ownership root. Every other policy reaches back to this.
-- ---------------------------------------------------------------------------
ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspaces FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workspace_isolation ON workspaces;
CREATE POLICY workspace_isolation ON workspaces
  USING (user_id = app_current_tenant())
  WITH CHECK (user_id = app_current_tenant());

-- ---------------------------------------------------------------------------
-- documents -- owned transitively through the workspace.
-- ---------------------------------------------------------------------------
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS document_isolation ON documents;
CREATE POLICY document_isolation ON documents
  USING (
    workspace_id IN (SELECT id FROM workspaces WHERE user_id = app_current_tenant())
  )
  WITH CHECK (
    workspace_id IN (SELECT id FROM workspaces WHERE user_id = app_current_tenant())
  );

-- ---------------------------------------------------------------------------
-- document_chunks -- two hops from the owner. This is the table that actually
-- holds document text and embeddings, so a gap here leaks content itself.
-- ---------------------------------------------------------------------------
ALTER TABLE document_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_chunks FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS document_chunk_isolation ON document_chunks;
CREATE POLICY document_chunk_isolation ON document_chunks
  USING (
    document_id IN (
      SELECT d.id FROM documents d
      JOIN workspaces w ON d.workspace_id = w.id
      WHERE w.user_id = app_current_tenant()
    )
  )
  WITH CHECK (
    document_id IN (
      SELECT d.id FROM documents d
      JOIN workspaces w ON d.workspace_id = w.id
      WHERE w.user_id = app_current_tenant()
    )
  );

-- ---------------------------------------------------------------------------
-- chat_sessions / chat_messages -- conversation history is tenant data too.
-- ---------------------------------------------------------------------------
ALTER TABLE chat_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_sessions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS chat_session_isolation ON chat_sessions;
CREATE POLICY chat_session_isolation ON chat_sessions
  USING (user_id = app_current_tenant())
  WITH CHECK (user_id = app_current_tenant());

ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_messages FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS chat_message_isolation ON chat_messages;
CREATE POLICY chat_message_isolation ON chat_messages
  USING (
    session_id IN (SELECT id FROM chat_sessions WHERE user_id = app_current_tenant())
  )
  WITH CHECK (
    session_id IN (SELECT id FROM chat_sessions WHERE user_id = app_current_tenant())
  );

-- Indexes on the columns every policy filters by. Without these, each policy
-- evaluation is a sequential scan on the parent table.
CREATE INDEX IF NOT EXISTS idx_workspaces_user_id ON workspaces (user_id);
CREATE INDEX IF NOT EXISTS idx_documents_workspace_id ON documents (workspace_id);
CREATE INDEX IF NOT EXISTS idx_document_chunks_document_id ON document_chunks (document_id);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_id ON chat_sessions (user_id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session_id ON chat_messages (session_id);

COMMIT;
