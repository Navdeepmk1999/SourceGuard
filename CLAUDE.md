# SourceGuard — Engineering Rules & Hand-Over

Instructions for anyone — human or AI — working in this repository. These are
not style preferences. Most encode a bug that has already happened once.

---

## Stack

| Layer | Technology |
| --- | --- |
| Backend | Python 3.11+, FastAPI (async), Pydantic v2, SQLAlchemy 2.0 async |
| Frontend | Next.js 16 App Router, React 19, TypeScript strict, Tailwind v4 |
| Database | Supabase PostgreSQL + pgvector (3072d), RLS-enforced |
| Cache | Upstash Redis — sliding-window rate limiting |
| Auth | Supabase, ES256 JWTs verified via JWKS |
| Embeddings | Google Gemini `gemini-embedding-001` |
| Generation | Groq, streamed |
| Ingestion | PyMuPDF — no system dependencies, no model downloads |
| Tests | pytest (backend) · Vitest + React Testing Library (frontend) |
| Hosting | Render (backend) · Vercel (frontend) |

---

## Commands

```bash
# Backend — 169 tests, ~7s, no network or services required
cd backend && source venv/bin/activate && pytest
pytest tests/test_api.py::TestClaimPersistence -q      # one class
ruff check app/                                        # lint

# Frontend — 81 tests, ~1s
cd frontend && npm test
npm run test:watch
npx tsc --noEmit        # typecheck — must be clean
npx eslint .            # lint — must be clean
npm run build           # must compile

# Database bootstrap (DDL; needs the ADMIN connection)
ADMIN_DATABASE_URL="postgresql+asyncpg://postgres:<pw>@<host>:5432/postgres" \
APP_DB_PASSWORD="<existing-password>" python -m app.db.init_db
```

Migrations in `backend/migrations/` apply in order with an admin connection.
They are idempotent; re-running is safe.

---

## Architectural rules

### 1. Always respect RLS

- The runtime `DATABASE_URL` **must** point at the restricted
  `sourceguard_app` role. PostgreSQL exempts `SUPERUSER` and `BYPASSRLS`
  roles from every policy unconditionally — a superuser connection silently
  disables tenant isolation while leaving the policies visible and correct.
- Verify any environment in one query:
  ```sql
  SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user;
  -- both must be false
  ```
- **Session-mode connections only** (Supabase port 5432). The tenant context
  is a session-scoped GUC; a transaction-mode pooler (6543) breaks it and can
  leak context across tenants.
- Never remove the teardown that clears `app.current_user_id`. A pooled
  connection would otherwise carry one user's context into another's request.
- New tenant-scoped tables need a policy in **both** `init_db.py` and a SQL
  migration, plus an index on whatever column the policy filters by.

### 2. Test isolation behaviourally, never by configuration

Asserting that a policy exists proves nothing — that is exactly the check
that passed while RLS was inert for several development cycles. Write the
test that connects as a second user and asserts **zero rows**.

### 3. Background tasks must fail gracefully and write to the database

- A background task has no caller. An exception vanishes into the event loop
  and the document sits in `processing` forever, so **every failure path must
  write to the row**.
- Record failures in a **fresh session**. Whatever failed may have left the
  original transaction aborted, where every further statement raises.
- Tasks run outside the request, so they outlive its session **and its RLS
  tenant context**. Use `tenant_session()`, never a bare `AsyncSessionLocal`.
- Accept an injectable `session_factory`. Background tasks bypass FastAPI's
  dependency overrides; without the seam, tests silently hit the real
  `DATABASE_URL`.
- **Check rowcount on status writes.** An `UPDATE` matching no rows commits
  happily — that is a silent failure, not a success.

### 4. Never report success for work that produced nothing

Zero chunks is a failure, not a completed ingestion. A document that appears
in the sidebar while being absent from every answer is worse than a visible
error.

### 5. `NULL` is not `[]`

For `chat_messages.claims`: `NULL` means *no verdicts recorded*, `[]` means
*verified, nothing flagged*. Collapsing them relabels an unverified answer as
clean — the exact false assurance this product exists to prevent. The
distinction is preserved in the column, the schema, the API client, and the
UI. Keep it that way.

### 6. Derive aggregates; do not store them

`overall_score` and `is_fully_supported` are computed from `claims` on read
via `aggregate_claims()`. One definition, shared by live verification and
history replay. A second copy drifts, and a restored answer would score
differently from the one the user originally saw.

### 7. Explicit error handling

Never catch bare `Exception` without re-raising or returning a structured
`HTTPException`. Log the specific exception server-side; return the generic
message to the client. An opaque message once hid a JWT algorithm mismatch
for an entire debugging cycle.

### 8. Fail-open vs fail-closed is a deliberate choice

| Dependency | On failure | Why |
| --- | --- | --- |
| Auth / JWKS | **Closed** (401) | Never allow unauthenticated access |
| Rate limiter | **Open** (allow) | A Redis outage must not take the API down |
| Embeddings | **Closed** (502) | A wrong-width vector poisons search silently |
| Telemetry | **Open** (drop) | Observability must never break a request |

Document any new choice at the call site.

### 9. Security

Never hardcode API keys, database credentials, or JWT secrets — load from the
environment. `ADMIN_DATABASE_URL` and `APP_DB_PASSWORD` are **bootstrap-only**
and must never be set on the running service: they carry DDL rights, and
granting the web service standing superuser access would hand every request
handler the privileges to drop the schema.

### 10. Modularity

Keep routes (`app/api/`), services (`app/services/`), models (`app/models/`),
and schemas (`app/schemas/`) separate. All I/O is async — `asyncpg`, `httpx`.
All request bodies are validated by Pydantic schemas with explicit bounds.

---

## Provider constraints that are easy to "fix" back

Encoded as `NOTE` comments with regression tests. All three return HTTP 400
from Gemini's OpenAI-compatibility layer if reintroduced:

1. **No `dimensions` field** in the embeddings payload. Matryoshka truncation
   is unavailable; `EMBEDDING_DIMENSIONS` must match the native width (3072).
2. **`input` must be a single string**, never a list. Batching is done
   client-side, one request per chunk.
3. **Pacing is not optional.** 15 requests/minute means 4 seconds between
   request *starts*. A per-task `sleep()` does not bound a rate — parallel
   workers sleep in parallel.

Changing `EMBEDDING_DIMENSIONS` requires dropping and recreating
`document_chunks`: `VECTOR(n)` is fixed at DDL time and `create_all` will not
alter an existing column.

---

## Testing conventions

- **Tests must not require network, PostgreSQL, or Redis.** The backend suite
  runs on in-memory SQLite with Redis faked and AI providers mocked. Keep it
  that way; a slow suite is a suite that stops being run.
- **Enable `PRAGMA foreign_keys=ON`** on SQLite test engines. Without it,
  `ON DELETE CASCADE` is a no-op and cascade tests pass vacuously.
- **Mock the narrowest thing.** Keep `ApiError` real when code branches on
  `instanceof` — a mocked class collapses the paths under test.
- **Mutation-test non-obvious guarantees.** Break the behaviour, confirm a
  test fails, restore. A test that passes against broken code is worse than
  no test.
- **Frontend:** globals are off; import `describe`/`it`/`expect` explicitly.
  RTL's `waitFor` deadlocks against fake timers — drain microtasks inside
  `act()` instead.
- PostgreSQL-only behaviour (RLS, `<=>`, JSONB) is verified against a live
  instance, not asserted in the SQLite suite.

---

## Known limitations

Do not describe these as solved:

- **Verification is a lexical heuristic, not a neural entailment model.**
  Despite the module name `nli_verifier.py` and NLI vocabulary, scoring is
  keyword coverage against retrieved chunks. It cannot detect negation or
  pure paraphrase. `_score_claim_against_chunk` is the intended swap point
  for a DeBERTa/NLI cross-encoder — decomposition, aggregation, thresholds,
  the SSE contract, and the UI all stay unchanged.
- **No OCR.** Scanned PDFs are rejected, not ingested.
- **Background tasks are in-process** and do not survive a restart.
- **3072 dimensions exceeds pgvector's 2000-dimension index ceiling.**
  Retrieval is a sequential scan; ANN indexing would need `halfvec`.
- **No staging environment.** Deliberate — see `README.md`.
