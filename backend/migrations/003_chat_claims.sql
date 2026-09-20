-- 003_chat_claims.sql
-- Persists per-claim verification verdicts alongside each assistant turn.
-- Idempotent; safe to re-run. Apply with an ADMIN connection, AFTER 002.
--
-- WHY
-- ---
-- Verification is the product: the whole point of SourceGuard is that an
-- answer arrives with every claim scored against its source. Those verdicts
-- were computed per response and never stored, so reopening a workspace
-- replayed the answers with no audit panel - which reads as "unverified"
-- rather than "verdicts were not kept".
--
-- SHAPE
-- -----
--   [{"claim": "...", "label": "entailed", "score": 0.83,
--     "supporting_chunk_index": 2}, ...]
--
-- `label` mirrors app/services/nli_verifier.py::EntailmentLabel
-- ('entailed' | 'not_entailed' | 'insufficient_evidence').
--
-- NULL vs []
-- ----------
-- NULL means "no verdicts stored" - a user turn, or an assistant turn written
-- before this column existed. [] means "verified, and nothing was flagged".
-- The UI renders those differently, so the distinction is load-bearing and
-- the column is deliberately left nullable with no default.
--
-- Aggregates (overall_score, is_fully_supported) are NOT stored. They are
-- derived from the claims on read by nli_verifier.aggregate_claims(), so a
-- stored summary cannot drift out of step with the claims it summarises.

BEGIN;

ALTER TABLE chat_messages
  ADD COLUMN IF NOT EXISTS claims jsonb;

COMMENT ON COLUMN chat_messages.claims IS
  'Per-claim verification verdicts for an assistant turn. NULL = not stored '
  '(user turn, or predates this column); [] = verified with nothing flagged.';

-- Partial index: only assistant turns carry verdicts, so indexing the rest
-- would be dead weight. Supports "show me answers with unsupported claims".
CREATE INDEX IF NOT EXISTS idx_chat_messages_claims
  ON chat_messages USING gin (claims)
  WHERE claims IS NOT NULL;

COMMIT;

-- Backfill is intentionally omitted. Verdicts cannot be reconstructed after
-- the fact: re-running the verifier would score old answers against today's
-- retrieved chunks, which are not the chunks those answers were generated
-- from. A fabricated audit trail is worse than an absent one, so historical
-- turns keep NULL and the UI states that verdicts are unavailable.
