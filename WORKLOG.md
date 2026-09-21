# SourceGuard — Build Log

A chronological record of how the system was built, including the decisions
that were reversed and why. Bugs are recorded where they were instructive;
several of the most useful ones produced no error at all.

---

## Phase 1 — Core RAG pipeline

**Foundation.** FastAPI with async SQLAlchemy 2.0, Pydantic v2 schemas, and a
module layout separating routes, services, models, and schemas. PostgreSQL
with `pgvector` for embeddings alongside relational metadata — one database
rather than a separate vector store, so chunks and their parent documents
commit in a single transaction and a partially-ingested document is
impossible.

**Portable vectors.** The test suite needed to run without a live PostgreSQL.
Rather than maintain a parallel mock schema, `PortableVector` was written as a
`TypeDecorator` that compiles to a native `VECTOR(n)` column on PostgreSQL and
a JSON-encoded `Text` column elsewhere. Tests exercise the real production
models; the seam is one class.

**Retrieval.** Dense pgvector cosine search plus PostgreSQL full-text,
combined by Reciprocal Rank Fusion. RRF was chosen over weighted blending
because cosine distance and `ts_rank` sit on incomparable scales, and any
weighting constant would be arbitrary and would drift as the corpus grew.
Query builders were written as pure functions returning SQL, making them
unit-testable without a database.

**Verification.** The differentiating feature: decompose each answer into
sentence-level claims, score each against the retrieved chunks, and label it.
Implemented as a deterministic lexical heuristic — keyword coverage with
word-boundary matching — rather than a neural model, to keep the pipeline
dependency-free and fully testable offline.

> A regression test here earned its place immediately: without `\b` anchors,
> "cat" counted as supported by chunks containing "category". A false
> `entailed` is the precise failure the product exists to prevent.

**Streaming.** Answers stream over SSE. The browser's native `EventSource` is
GET-only and the query endpoint is a POST with a JSON body, so the frontend
hand-parses frames off `fetch` + `ReadableStream`. Verified against the real
`sse-starlette` wire format, which is CRLF-terminated — a parser splitting on
`\n` alone produces subtly corrupted JSON.

---

## Phase 2 — Multi-tenancy and authentication

**Supabase Auth.** JWT verification began with the legacy HS256 shared-secret
scheme and rejected every real token. The failure was opaque until a
temporary debug line revealed `InvalidAlgorithmError`: the project's JWKS
endpoint published an **ES256** key. Rewritten to verify asymmetrically via
`PyJWKClient` against the project's JWKS, with the key set cached so a brief
Supabase blip doesn't reject valid tokens.

> The lesson generalised: the generic client-facing message ("Invalid or
> expired token") had hidden the root cause for an entire debugging cycle.
> Specific exceptions are now logged server-side while the client still
> receives the generic message.

**Row-Level Security — and the bug that looked like success.** Policies were
written on every tenant table. They were syntactically correct, visible in
`pg_policies`, and **enforcing nothing**, because the application connected as
`postgres` — and PostgreSQL exempts `SUPERUSER` and `BYPASSRLS` roles from
every policy unconditionally.

It surfaced from a *behavioural* test: connect as a second user, confirm they
can read the first user's rows. A configuration audit would have shown green.

Three things came out of the fix:

1. `init_db` now provisions a restricted `sourceguard_app` role —
   `NOSUPERUSER NOBYPASSRLS`, CRUD only, never DDL, deliberately not the
   table owner.
2. A widely-held misconception was disproved against a live database:
   `FORCE ROW LEVEL SECURITY` does **not** constrain a superuser. It extends
   policies to the table owner. A code comment claiming otherwise was
   corrected.
3. Enforcing RLS exposed a second, latent bug. The tenant variable was
   transaction-scoped, and several endpoints commit mid-request, so every
   query after the first ran with no tenant context. Invisible while the
   policies were inert, because the superuser bypassed them anyway. Moved to
   session scope, with a teardown reset so a pooled connection cannot carry
   one user's context into another's request.

**Ownership checks.** A user-reported crash turned out to be a stale
`--reload` artifact — but investigating it surfaced a real defect: several
endpoints checked that a workspace *existed* and never that it belonged to
the caller. `ensure_workspace_owner` was added, returning an identical 404
for "absent" and "not yours" so IDs cannot be enumerated.

---

## Phase 3 — Production constraints

**Containerisation and CI.** Multi-stage Docker builds, non-root users, and a
GitHub Actions pipeline running pytest, typecheck, lint, and build. No service
containers: the suite runs on SQLite with Redis faked and AI providers mocked,
so CI needs no Postgres, no Redis, and no network egress.

> Two Next.js traps cost real time. `NEXT_PUBLIC_*` are inlined at **build**
> time, so passing them at `docker run` bakes in `undefined` — the app starts
> and cannot reach the API. And standalone output omits `public/` and
> `.next/static/`, so the container boots cleanly and 404s every stylesheet.

**The embedding provider migration.** Switching to Gemini's
OpenAI-compatibility layer took several iterations, each one a genuine
constraint discovered by hitting it:

- The `dimensions` field is **rejected with a 400**, so Matryoshka truncation
  is unavailable and the column must match the native width.
- `input` must be a **single string** — a list is also a 400 — so batching
  moved client-side: one request per chunk, issued concurrently.
- Settling the true output width took two corrections in both directions
  before landing on 3072.

Each constraint is now recorded as a `NOTE` in the request builder and backed
by a regression test, because all three are natural things to "fix" back.

**Rate limiting the outbound path.** One request per chunk against a 15/min
quota produced `429`s immediately. The instinctive fix — a semaphore plus a
sleep — does not bound a rate: two workers each sleeping two seconds still
issue ~60 requests/minute, because they sleep in parallel.

What bounds a rate is the spacing between request *starts*. A pacer
serialises reservations behind a lock at `60/15 = 4` seconds, with a
semaphore capping in-flight connections at 2 and exponential backoff
(proportional jitter, `Retry-After` honoured) absorbing the rest. Only 429 is
retried.

> The first backoff used a flat `random.uniform(0, 1)` jitter term, which
> didn't scale to zero — the test suite took 10 s instead of 5. Making jitter
> proportional to the delay fixed both the speed and the algorithm.

**Asynchronous ingestion.** Correct pacing made ingestion slow by
construction: a 50-chunk PDF needs over three minutes of wall clock, well
beyond any HTTP request lifetime. Upload became `202 Accepted` with a
per-document status URL, the work moved to a FastAPI `BackgroundTask`, and a
frontend polling hook drives the UI to completion.

`BackgroundTasks` was chosen over ARQ or Celery deliberately: both need a
separate worker process, which the free tier doesn't provide. The cost — an
in-process task doesn't survive a restart — is mitigated by a reaper that
fails documents stranded in `processing`.

Two things broke in instructive ways:

- A background task outlives the request **and its database session**,
  including the RLS tenant context. It needed `tenant_session()`, a factory
  that re-establishes that context on its own connection. It also bypassed
  FastAPI's dependency overrides, meaning tests were quietly talking to the
  real `DATABASE_URL` until an injectable session factory was added.
- A PDF with no text layer produced zero chunks and committed as
  **`completed`**. No exception, no log, a green `/health` — and a document
  that appeared ingested while being absent from every answer. Reproduced
  against live PostgreSQL, then made a failure with an actionable message.

**Two silent CORS failures.** Both produced a healthy service and a dead
frontend. First, a blank `CORS_ALLOWED_ORIGINS` split into an empty list,
which `CORSMiddleware` enforces as *deny every origin* — diagnosed by probing
the live preflight and observing that even the default `localhost` origin was
rejected. Second, JSON-array values were comma-split into fragments after a
`NoDecode` annotation disabled pydantic-settings' own JSON parsing. Blank now
falls back to the default with a warning, both encodings parse, and the
explicit method allow-list was replaced with a wildcard after it silently
broke `DELETE` preflight the moment deletion routes were added.

---

## Phase 4 — Feature completeness

**Cascading deletion.** `DELETE` endpoints for workspaces and documents,
relying on `ON DELETE CASCADE` at the foreign-key level rather than the ORM's
delete-orphan — one statement in the database instead of thousands of loaded
objects. Both return exact cascade counts, because the blast radius is
otherwise invisible: the caller asked to delete one workspace and may have
destroyed thousands of embeddings.

> Writing the tests revealed that SQLite ignores `ON DELETE CASCADE` unless
> foreign keys are enabled per connection. The existing cascade test had been
> passing **vacuously** via ORM-level delete-orphan. `PRAGMA foreign_keys=ON`
> now makes the tests exercise the behaviour production actually relies on.

**Frontend test infrastructure.** Vitest with React Testing Library and jsdom.
Adding it required bumping `@types/node` from a stale `^20` — matching neither
CI nor local — to `^22`, and dropping `vite-tsconfig-paths` once Vite 8 made
path resolution native.

> RTL's `waitFor` deadlocks against `vi.useFakeTimers`, timing out five hook
> tests. Since the clock is fully controlled in those tests, waiting is
> deterministic: a `flush()` helper draining microtasks inside `act()`
> replaced it.

**Auth UI.** Login, signup, password reset, and resend-confirmation. The
resend sits behind a 60-second cooldown because Supabase rate-limits it with
an unhelpful 429, and its confirmation is worded to reveal nothing about
whether an address is registered. A show/hide toggle covers both password
fields together, and signup validates the confirmation field **before**
calling Supabase — which has no confirmation concept, so a mistyped password
would otherwise create a real account the user cannot sign into.

> The middleware initially treated only `/login` as public, making password
> reset unreachable: a user who has forgotten their password is by definition
> signed out. `/reset-password` needed exempting for the opposite reason —
> Supabase exchanges the recovery token for a real session, so the visitor
> arrives *authenticated* and was redirected away before choosing a password.

**Session persistence and cleanup.** `WorkspaceContext` subscribes to
Supabase's `onAuthStateChange`: `SIGNED_IN` loads that user's workspaces,
`SIGNED_OUT` wipes workspaces, active selection, and error state
synchronously, so no frame can paint the previous user's dashboard.
`TOKEN_REFRESHED` is deliberately ignored — identity has not changed.

> The callback is deliberately **not** `async`. Supabase serialises auth calls
> behind a lock, and fetching workspaces resolves a JWT — itself an auth call
> — so awaiting inside the callback deadlocks. The fetch defers to a
> macrotask; the sign-out wipe stays synchronous.

**Chat history.** `GET /workspaces/{id}/history` replays a workspace's most
recent conversation. It returns the **`session_id`**, which matters as much as
the messages: without it the client would render the restored thread and then
open a *new* session on the next question, so the model would answer with no
memory of what is visibly on screen. Only the newest session replays —
concatenating sessions would splice unrelated conversations into one.

**Persisting the audit trail.** The final gap, and the most important:
verdicts were computed per response and never stored, so a restored
conversation showed answers stripped of every verdict — which reads as
*unverified*, a stronger and wronger claim than *not recorded*.

Migration `003_chat_claims.sql` added a `claims` JSONB column with a partial
GIN index. Verdicts are written in a two-step: the answer persists when
streaming ends, verdicts attach afterwards, so a verifier failure costs the
audit trail rather than the response. Aggregates are derived on read from a
shared function, so a stored summary can never drift from the claims it
summarises. `NULL` and `[]` are kept rigorously distinct end to end, and
historical rows are deliberately not backfilled — re-running the verifier
would score old answers against today's chunks, and a fabricated audit trail
is worse than an absent one.

---

## Current state

**250 automated tests** — 169 backend, 81 frontend — running in roughly seven
seconds combined, with no network, database, or service dependencies.
Typecheck, lint, and production build are clean.

Behaviour the test suite structurally cannot cover was verified against a
live PostgreSQL instance with the restricted role: RLS enforcement, cascade
semantics, and JSONB round-tripping.

Throughout, non-obvious guarantees were checked by **mutation testing** —
breaking the behaviour deliberately and confirming a test fails. It caught
several assertions that would have passed against broken code, which is worse
than having no test at all.
