<div align="center">

# SourceGuard

**Retrieval-augmented answers with a per-claim audit trail.**

Every sentence an answer makes is decomposed, scored against the retrieved
source, and labelled — before the reader is asked to trust it.

[![Backend Tests](https://img.shields.io/badge/backend%20tests-169%20passing-brightgreen)](#testing)
[![Frontend Tests](https://img.shields.io/badge/frontend%20tests-81%20passing-brightgreen)](#testing)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Next.js](https://img.shields.io/badge/Next.js-16-000000?logo=nextdotjs&logoColor=white)](https://nextjs.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-strict-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector%203072d-4169E1?logo=postgresql&logoColor=white)](https://github.com/pgvector/pgvector)
[![Supabase](https://img.shields.io/badge/Supabase-Auth%20%2B%20DB-3FCF8E?logo=supabase&logoColor=white)](https://supabase.com/)
[![Tenant Isolation](https://img.shields.io/badge/tenant%20isolation-DB--enforced%20RLS-critical)](#multi-tenancy-enforced-by-the-database-not-the-orm)

</div>

---

## Executive summary

Two things block LLM adoption inside organisations that actually have
something to lose: **answers that sound right and aren't**, and **data that
must not leak between customers**. SourceGuard is built around both.

**On hallucination.** Standard RAG reduces fabrication but cannot eliminate
it, and citations don't close the gap — a citation asserts that a chunk is
*related*, not that a sentence is *supported*. SourceGuard decomposes each
generated answer into individual claims, scores every claim against the
chunks actually retrieved for that query, and streams the verdicts into a
live audit panel:

| Verdict | Meaning |
| --- | --- |
| 🟢 `entailed` | The retrieved source supports this claim |
| 🔴 `not_entailed` | Partial support — treat with caution |
| 🟡 `insufficient_evidence` | The source does not substantiate this claim |

Those verdicts are **persisted**, so reopening a conversation months later
replays the audit trail rather than the bare text.

**On isolation.** Tenant separation is enforced by PostgreSQL Row-Level
Security, not by application code remembering to add a `WHERE` clause. A
forgotten filter in a new endpoint cannot leak another tenant's data, because
the database refuses to return it.

---

## Key features

**Verified generation**
- Sentence-level claim decomposition with a per-claim verdict and score
- Word-boundary matching (`\bcat\b` never matches "category") — the
  false-positive class this product exists to prevent
- Verdicts stored as JSONB and replayed on reload, with `NULL` (not recorded)
  kept distinct from `[]` (verified, nothing flagged)

**Multi-tenancy**
- RLS policies on all five tenant tables, enforced via a restricted,
  non-superuser database role
- Supabase JWT (ES256, verified against JWKS) → Postgres session variable →
  policy evaluation
- Application-layer ownership checks alongside RLS, returning an identical
  404 for "absent" and "not yours" so IDs can't be enumerated

**Asynchronous ingestion**
- Upload returns `202 Accepted` with a per-document status URL
- Layout-aware PDF parsing: tables extracted to Markdown, headings detected
  relative to each document's own body font size
- Semantic chunking — tables stay atomic, headings bind to their content
- Frontend polling hook drives the UI from *pending* through to *completed*

**Production engineering**
- Custom request pacer and exponential backoff around a 15 req/min API quota
- Cascading deletes that report exact blast radius before and after
- Complete auth suite: login, signup, password reset, resend confirmation
- **250 automated tests** (169 backend, 81 frontend), no network required

---

## System architecture

```mermaid
flowchart TB
    subgraph client["Browser"]
        UI["Next.js 16 · App Router<br/>Streaming chat + audit panel"]
    end

    subgraph vercel["Vercel"]
        FE["Static + edge runtime"]
    end

    subgraph render["Render · FastAPI"]
        AUTH["JWT verification<br/>ES256 via JWKS"]
        RL["Rate limiter<br/>Redis sliding window"]
        TEN["Tenant context<br/>SET app.current_user_id"]
        API["REST + SSE endpoints"]
        BG["BackgroundTasks<br/>parse → chunk → embed"]
        VER["Verification<br/>claim decomposition + scoring"]
    end

    subgraph data["Managed data"]
        PG[("Supabase Postgres<br/>pgvector 3072d · RLS")]
        RD[("Upstash Redis")]
    end

    subgraph ext["External APIs"]
        SB["Supabase Auth"]
        GEM["Google Gemini<br/>embeddings"]
        GROQ["Groq<br/>generation"]
    end

    UI --> FE
    FE -->|"Bearer JWT"| AUTH
    AUTH -.->|"fetch JWKS"| SB
    AUTH --> RL
    RL <--> RD
    RL --> TEN
    TEN --> API
    API -->|"202 Accepted"| BG
    BG -->|"paced 15/min"| GEM
    BG --> PG
    API --> PG
    API --> VER
    VER -->|stream| GROQ
    VER -->|"SSE: token · verification · done"| UI
```

### Query lifecycle

```mermaid
sequenceDiagram
    participant B as Browser
    participant A as FastAPI
    participant P as Postgres
    participant L as Groq

    B->>A: POST /query/stream (JWT)
    A->>A: Verify ES256 JWT via JWKS
    A->>P: SET app.current_user_id → RLS active
    A->>P: Hybrid search (pgvector + FTS → RRF)
    A->>P: Load conversation window
    A-->>B: event: session
    loop Streaming
        L-->>A: token
        A-->>B: event: token
    end
    A->>A: Decompose answer into claims
    loop Per claim
        A-->>B: event: verification
    end
    A->>P: Persist turns + claims (JSONB)
    A-->>B: event: done
```

---

## Engineering highlights

### Asynchronous ingestion, because the work outlives the request

Embedding is paced to 15 requests per minute by the provider's free tier, and
Gemini's OpenAI-compatibility layer rejects batched input — so each chunk is
its own request. A 50-chunk PDF therefore takes **over three minutes of
wall-clock**, far longer than any browser, proxy, or platform will hold an
HTTP connection open. Synchronous upload wasn't slow; it was impossible.

Upload now returns `202 Accepted` immediately with a per-document status URL,
and the work runs in a FastAPI `BackgroundTask`. The interesting part is what
that breaks. A background task outlives the request, so it outlives the
request's database session — including the tenant context that RLS depends
on. It needed its own session factory that re-establishes that context, or
every insert would be rejected by the very policies meant to govern it.

Choosing `BackgroundTasks` over a broker-backed queue (ARQ, Celery) was a
deliberate constraint-driven trade: those require a separate worker process,
which the free tier doesn't offer. The cost is honest and documented — an
in-process task doesn't survive a restart — so a reaper marks documents
stranded in `processing` as failed rather than leaving the UI polling forever.

The subtlest bug here produced no error at all: a PDF with no text layer
yields zero chunks, and the pipeline committed that as `completed`. The
upload looked successful, the document appeared in the sidebar, and it was
silently absent from every answer. Zero chunks is now a failure with an
actionable message.

### Rate limiting that actually bounds the rate

The obvious fix for `429 Too Many Requests` is a semaphore plus a sleep. It
doesn't work: two concurrent workers each sleeping two seconds still issue
roughly 60 requests per minute — four times the quota — because they sleep in
parallel.

What bounds a *rate* is the spacing between request **starts**. A small pacer
serialises reservations behind a lock so no two requests begin closer than
`60 / 15 = 4` seconds apart, holding the average at exactly the quota
regardless of how long each call takes. The semaphore (capped at 2) then
bounds in-flight connections, and exponential backoff with **proportional**
jitter handles the 429s that still slip through — honouring `Retry-After`
when the server sends it, since that's the provider stating precisely when
the window reopens.

Only `429` is retried. A `400` is deterministic; retrying it burns quota and
delays an error the caller needs to see.

### Multi-tenancy enforced by the database, not the ORM

This is the part of the system I'd most want reviewed, because the first
implementation was wrong in a way that looked right.

RLS policies existed on every tenant table. They were syntactically correct
and visible in `pg_policies`. They were enforcing **nothing** — because the
application connected as `postgres`, and PostgreSQL exempts `SUPERUSER` and
`BYPASSRLS` roles from every policy *unconditionally*. No table-level flag
overrides that; `FORCE ROW LEVEL SECURITY` extends policies to the table
owner, not to a superuser. A configuration audit would have shown green.

The fix was a restricted `sourceguard_app` role provisioned `NOSUPERUSER
NOBYPASSRLS` with CRUD grants only — never DDL, and deliberately not the table
owner. Enforcing RLS then exposed a second, latent bug: the tenant variable
was transaction-scoped, and several endpoints commit mid-request, so every
query after the first ran with no tenant set. Invisible while the policies
were inert, because the superuser bypassed them anyway.

Isolation is now verified **behaviourally** — a second user reading the first
user's rows returns zero rows — rather than by asserting the policies exist.
Any deployment can be checked in one query:

```sql
SELECT current_user, rolsuper, rolbypassrls
FROM pg_roles WHERE rolname = current_user;
-- rolsuper and rolbypassrls must BOTH be false
```

### Persisting the audit trail

Verification verdicts were computed per response and never stored, so
reopening a workspace replayed the answers stripped of every verdict — which
reads as *unverified*, a stronger and wronger claim than *not recorded*.

A `claims` JSONB column now stores per-claim verdicts, written in a
deliberate two-step: the answer is persisted the moment streaming ends, and
verdicts are attached afterwards, so a verifier failure costs the audit trail
for that turn rather than the response itself. Aggregates (`overall_score`,
`is_fully_supported`) are **derived on read** from a shared function rather
than stored, so a summary can never drift out of step with the claims it
summarises.

`NULL` and `[]` are kept rigorously distinct throughout — `NULL` means no
verdicts were recorded, `[]` means verified with nothing flagged. Collapsing
them would relabel an unverified answer as clean, which is precisely the
false assurance this product exists to prevent. Historical rows are
deliberately **not** backfilled: re-running the verifier would score old
answers against today's retrieved chunks, and a fabricated audit trail is
worse than an absent one.

---

## The "no staging environment" trade-off

There is no staging server. That was a decision, not an omission.

A staging environment costs a second copy of every managed service and a
continuous reconciliation burden, and it catches a class of bug — environment
drift — that this stack largely doesn't have: the same Docker image runs
locally, in CI, and in production, and infrastructure is declarative. Paying
that cost for a single-maintainer project would have bought less safety per
hour than the alternative.

The risk is carried by four things instead:

1. **250 automated tests** that require no network, no Postgres, and no Redis
   — the suite runs in ~7 seconds, so it's actually run.
2. **Mutation testing.** Every non-obvious guarantee was verified by breaking
   it and confirming a test fails. A test that passes against broken code is
   worse than no test, and this caught several.
3. **Vercel preview deployments**, giving every frontend change a real URL on
   real infrastructure before it reaches production.
4. **Verified-by-behaviour checks for anything the test suite structurally
   cannot cover** — RLS enforcement and cascade semantics were exercised
   against a live PostgreSQL instance with the restricted role, because the
   SQLite test database has no RLS to enforce.

The honest limitation: this catches logic regressions well and
infrastructure-interaction bugs poorly. The mitigation is that the surface
where those bugs live is small and documented.

---

## Tech stack

| Layer | Technology |
| --- | --- |
| Frontend | Next.js 16 (App Router), React 19, TypeScript strict, Tailwind CSS v4 |
| Backend | Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2.0 (async) |
| Database | Supabase PostgreSQL + pgvector (3072d), RLS-enforced |
| Cache | Upstash Redis — sliding-window rate limiting via atomic Lua |
| Auth | Supabase (ES256 / JWKS) |
| Embeddings | Google Gemini (`gemini-embedding-001`) |
| Generation | Groq (streaming) |
| Ingestion | PyMuPDF — layout-aware, no system dependencies |
| Hosting | Vercel (frontend) · Render (backend) |
| Testing | pytest · Vitest · React Testing Library |

---

## Getting started

### Prerequisites

Python 3.11+, Node.js 20+, Docker.

```bash
docker run -d --name sourceguard-db -p 5432:5432 \
  -e POSTGRES_PASSWORD=postgres pgvector/pgvector:pg16
docker run -d --name sourceguard-redis -p 6379:6379 redis:7-alpine
```

### Backend

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Bootstrap: tables, pgvector, the restricted app role, and RLS policies.
# Requires the ADMIN (superuser) connection — DDL only.
ADMIN_DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/sourceguard" \
APP_DB_PASSWORD="choose-a-password" python -m app.db.init_db

uvicorn app.main:app --reload
```

Then create `backend/.env`, pointing `DATABASE_URL` at the **restricted role**:

```bash
DATABASE_URL=postgresql+asyncpg://sourceguard_app:<password>@localhost:5432/sourceguard
SUPABASE_URL=https://<project-ref>.supabase.co
```

> Pointing `DATABASE_URL` at a superuser silently disables RLS. On Supabase,
> use the **session-mode** connection (port 5432), never the transaction
> pooler (6543) — the tenant context is a session-scoped variable.

### Frontend

```bash
cd frontend
npm install
npm run dev     # http://localhost:3000
```

```bash
# frontend/.env.local
NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
NEXT_PUBLIC_SUPABASE_URL=https://<project-ref>.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=<publishable-key>
```

**With no AI provider keys set, the entire pipeline runs offline** on
deterministic mocks — including the full test suite.

---

## Testing

```bash
cd backend && source venv/bin/activate && pytest       # 169 tests, ~7s
cd frontend && npm test                                # 81 tests, ~1s
```

The backend suite runs entirely against in-memory SQLite — no Postgres, no
Redis, no network. That's possible because `PortableVector` compiles to a
native `pgvector` column on PostgreSQL and a JSON-encoded `Text` column
elsewhere, so tests exercise the **real production ORM models** rather than a
parallel mock schema.

PostgreSQL-specific behaviour that SQLite cannot express — RLS enforcement,
the `<=>` operator, JSONB round-tripping — is verified directly against a
live instance.

---

## Documentation

| Document | Contents |
| --- | --- |
| [`DESIGN.md`](DESIGN.md) | Schema, RLS strategy, and the verification pipeline |
| [`WORKLOG.md`](WORKLOG.md) | Chronological build history |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | Render + Vercel deployment, with AWS as an alternative |
| [`CLAUDE.md`](CLAUDE.md) | Engineering rules and hand-over notes |

---

## Known limitations

Stated plainly, because a portfolio project that claims none isn't credible:

- **Verification is lexical, not neural.** Claims are scored by keyword
  coverage against retrieved chunks, not by a trained entailment model. It
  cannot detect negation ("the contract does *not* expire") or pure
  paraphrase. The module is deliberately structured so the scoring function
  can be replaced by a DeBERTa/NLI cross-encoder without touching
  decomposition, aggregation, thresholds, the SSE contract, or the UI.
- **No OCR.** Scanned PDFs have no text layer and are rejected with a
  message saying so rather than silently ingesting as empty.
- **Background tasks are in-process.** They don't survive a restart; stranded
  documents are reaped rather than resumed.
- **3072 dimensions exceeds pgvector's 2000-dimension ceiling for `ivfflat`
  and `hnsw` indexes.** Retrieval is currently a sequential scan, which is
  fine at present corpus size; ANN indexing would require the `halfvec` type.
