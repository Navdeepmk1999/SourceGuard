<div align="center">

# SourceGuard

**Retrieval-augmented answers, verified against source.**

Every claim in a generated answer is decomposed and checked against the
retrieved context *before* it reaches the user — not just cited, but scored.

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Next.js](https://img.shields.io/badge/Next.js-16-000000?logo=nextdotjs&logoColor=white)](https://nextjs.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-strict-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector-4169E1?logo=postgresql&logoColor=white)](https://github.com/pgvector/pgvector)
[![Redis](https://img.shields.io/badge/Redis-rate--limiting-DC382D?logo=redis&logoColor=white)](https://redis.io/)
[![Docker](https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![Render](https://img.shields.io/badge/Render-backend-46E3B7?logo=render&logoColor=white)](https://render.com/)
[![Vercel](https://img.shields.io/badge/Vercel-frontend-000000?logo=vercel&logoColor=white)](https://vercel.com/)
[![Terraform](https://img.shields.io/badge/Terraform-AWS_ECS_(alt)-7B42BC?logo=terraform&logoColor=white)](https://www.terraform.io/)
[![Tests](https://img.shields.io/badge/tests-114_passing-brightgreen)](#testing)
[![RLS](https://img.shields.io/badge/tenant_isolation-DB--enforced_RLS-critical)](#security)

</div>

---

## The problem

Standard RAG reduces hallucination but does not eliminate it. The model still
produces fluent prose that *sounds* grounded, and the reader has no way to
tell which sentences the source documents actually support. Citations point at
evidence; they do not evaluate it. In legal, financial, or compliance work,
that leaves the reviewer re-reading the sources anyway — erasing the gain.

**SourceGuard evaluates.** It decomposes each generated answer into individual
claims, scores every claim against the retrieved context, and streams the
verdicts into a live audit panel:

| Verdict | Meaning |
| --- | --- |
| 🟢 `entailed` | The retrieved source supports this claim |
| 🔴 `not_entailed` | Partial support — treat with caution |
| 🟡 `insufficient_evidence` | The source does not substantiate this claim |

---

## Architecture

```mermaid
flowchart TB
    subgraph client["Client"]
        UI["Next.js 16 · App Router<br/>Streaming chat + audit panel"]
    end

    subgraph api["FastAPI Backend"]
        direction TB
        AUTH["JWT verification<br/>ES256 via JWKS"]
        RL["Rate limiter<br/>Redis sliding window"]
        RLS_CTX["Tenant context<br/>SET app.current_user_id"]
        ING["Ingestion<br/>layout parse → semantic chunk → embed"]
        RET["Hybrid retrieval<br/>pgvector ANN + full-text → RRF"]
        GEN["Generation<br/>Groq token stream"]
        VER["Verification<br/>claim decomposition + entailment"]
    end

    subgraph data["Data"]
        PG[("PostgreSQL + pgvector<br/>RLS enforced")]
        RD[("Redis")]
    end

    subgraph ext["External"]
        SB["Supabase Auth"]
        GQ["Groq"]
        TG["Together AI"]
    end

    UI -->|"Bearer JWT"| AUTH
    AUTH -.->|"fetch JWKS"| SB
    AUTH --> RL
    RL <--> RD
    RL --> RLS_CTX
    RLS_CTX --> ING
    RLS_CTX --> RET
    ING -->|embeddings| TG
    ING --> PG
    RET <--> PG
    RET --> GEN
    GEN -->|stream| GQ
    GEN --> VER
    VER -->|"SSE: token · verification · done"| UI
```

### Query lifecycle

```mermaid
sequenceDiagram
    participant B as Browser
    participant A as FastAPI
    participant R as Redis
    participant P as Postgres
    participant L as Groq

    B->>A: POST /query/stream (JWT)
    A->>A: Verify ES256 JWT via JWKS
    A->>R: Sliding-window check (atomic Lua)
    A->>P: SET app.current_user_id → RLS active
    A->>P: Hybrid search (pgvector + FTS → RRF)
    A->>P: Load last 10 conversation turns
    A-->>B: event: session
    loop Streaming
        A->>L: Generate with context + history
        L-->>A: token
        A-->>B: event: token
    end
    A->>A: Decompose answer into claims
    loop Per claim
        A-->>B: event: verification
    end
    A->>P: Persist both turns
    A-->>B: event: done
```

---

## Features

**Verified generation**
- Sentence-level claim decomposition with per-claim entailment scoring
- Word-boundary–enforced matching (`\bcat\b` never matches "category" — a
  false-positive class this product exists to prevent)
- Live audit panel populated as verdicts arrive, not after the fact

**Layout-aware ingestion**
- Tables extracted via PyMuPDF `find_tables()` and rendered to **Markdown**, so
  row/column relationships survive into the embedding
- Headings detected *relative* to each document's own body font size
- Chunking on semantic boundaries: tables stay atomic, headings bind to the
  content they introduce, splits land between elements — never mid-sentence

**Hybrid retrieval**
- Dense pgvector ANN + sparse Postgres full-text search
- Merged by **Reciprocal Rank Fusion** (rank-position based, so two
  incomparable score scales never need arbitrary normalization)

**Conversation memory**
- Multi-turn sessions with a 10-message sliding window
- History injected as role-attributed messages, not flattened into the prompt

**Production engineering**
- 114 backend tests, no network or services required (~5s)
- Multi-stage Docker builds, non-root, Next.js standalone output
- GitHub Actions CI: pytest + typecheck + lint + build
- Deployed on Render + Vercel; Terraform for AWS ECS Fargate retained as the
  enterprise-scale alternative

---

## Security

### Database-enforced tenant isolation

Row-Level Security is **enforced by PostgreSQL**, not merely defined, on all
five tenant tables: `workspaces`, `documents`, `document_chunks`,
`chat_sessions`, `chat_messages`.

This distinction is the point. PostgreSQL exempts `SUPERUSER` and `BYPASSRLS`
roles from every policy **unconditionally** — so an application connecting as
`postgres` gets policies that are syntactically correct, visible in
`pg_policies`, and enforcing nothing.

SourceGuard therefore connects as a restricted `sourceguard_app` role that
`init_db` provisions with `NOSUPERUSER NOBYPASSRLS`, granted CRUD only — never
DDL, and deliberately not the table owner (an owner bypasses RLS absent
`FORCE`).

Verify any deployment in one query:

```sql
SELECT current_user, rolsuper, rolbypassrls
FROM pg_roles WHERE rolname = current_user;
-- rolsuper and rolbypassrls must BOTH be false
```

Isolation is verified behaviorally — a second user reading the first user's
rows returns **zero rows** — rather than by asserting the policies exist.

### Additional controls

| Control | Implementation |
| --- | --- |
| Authentication | Supabase ES256 JWTs verified via JWKS; fails closed on missing/expired/wrong-audience tokens |
| Defense in depth | Application-layer ownership checks alongside RLS; identical 404 for "absent" and "not yours", so IDs cannot be enumerated |
| Rate limiting | Per-`user_id` sliding window (10 queries/min, 20 uploads/min) via an atomic Redis Lua script |
| Upload hardening | Path-traversal and disguised-extension rejection before any parsing; fail-fast batches |
| Secrets | Loaded from environment (platform env vars on Render/Vercel; SSM ARNs on the AWS path), never baked into images or Terraform state |
| CORS | Explicit origin allow-list, no wildcards |

---

## Tech stack

| Layer | Technology |
| --- | --- |
| Backend | Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2.0 (async) |
| Database | PostgreSQL + pgvector, RLS-enforced |
| Cache | Redis (sliding-window rate limiting) |
| Frontend | Next.js 16 (App Router), TypeScript strict, Tailwind CSS v4 |
| Auth | Supabase (ES256 / JWKS) |
| AI | Groq (generation), Together AI (embeddings) — both with offline mocks |
| Ingestion | PyMuPDF layout parsing (no system dependencies) |
| Observability | LangSmith via explicit `@traceable` spans |
| Hosting | **Render** (backend) · **Vercel** (frontend) · Supabase · Upstash |
| Infra | Docker, Docker Compose, GitHub Actions; Terraform/AWS ECS Fargate as an alternative |

---

## Quick start

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

Then create `backend/.env`, pointing `DATABASE_URL` at the **restricted role**
just created:

```bash
DATABASE_URL=postgresql+asyncpg://sourceguard_app:<password>@localhost:5432/sourceguard
SUPABASE_URL=https://<project-ref>.supabase.co
```

> Pointing `DATABASE_URL` at a superuser silently disables RLS. See
> [Security](#security).
>
> On Supabase, `DATABASE_URL` must also use a **session-mode** connection
> (port **5432**), never the transaction-mode pooler (port **6543**). The RLS
> tenant context is a session-scoped variable; transaction-mode pooling
> returns the connection between transactions, which breaks the context and
> can leak it across tenants. See [`DEPLOYMENT.md`](DEPLOYMENT.md).

### Frontend

```bash
cd frontend
npm install
npm run dev     # http://localhost:3000
```

Create `frontend/.env.local`:

```bash
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000/api/v1
NEXT_PUBLIC_SUPABASE_URL=https://<project-ref>.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=<publishable-key>
```

> Use `http://localhost:3000`, **not** `http://127.0.0.1:3000` — the CORS
> allow-list contains the former only, and browsers treat them as distinct
> origins.

**With no AI provider keys set, the entire pipeline runs offline** on
deterministic mocks — including the full test suite.

### Environment reference

| Variable | Required | Notes |
| --- | --- | --- |
| `DATABASE_URL` | yes | Must be the restricted, non-superuser role. On Supabase, use the **session-mode** connection (port **5432**) — see below |
| `ADMIN_DATABASE_URL` | bootstrap | Superuser connection, used only by `init_db` |
| `APP_DB_PASSWORD` | bootstrap | Password assigned to the restricted role |
| `APP_DB_ROLE` | no | Defaults to `sourceguard_app` |
| `SUPABASE_URL` | yes | Project URL; builds the JWKS endpoint. Not a secret |
| `REDIS_URL` | no | Defaults to `redis://localhost:6379/0` |
| `GROQ_API_KEY` | no | Unset ⇒ deterministic offline mock generation |
| `TOGETHER_API_KEY` | no | Unset ⇒ deterministic offline mock embeddings |
| `LANGSMITH_API_KEY` | no | Required for tracing |
| `LANGCHAIN_TRACING_V2` / `LANGSMITH_TRACING` | no | `true` enables tracing |

---

## Docker

```bash
docker compose up --build
```

Frontend → `http://localhost:3000` · Backend → `http://localhost:8000`

Postgres and Redis are intentionally **not** in `docker-compose.yml` — the
local setup already runs them on the standard ports, and redeclaring them
would collide on 5432/6379. The services reach them via
`host.docker.internal`.

`NEXT_PUBLIC_*` are inlined at **build** time, so they are passed as build
args rather than runtime environment:

```bash
export NEXT_PUBLIC_SUPABASE_URL=https://<ref>.supabase.co
export NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=<publishable-key>
docker compose up --build
```

`NEXT_PUBLIC_API_URL` defaults to `http://localhost:8000/api/v1`, **not**
`http://backend:8000` — that URL is fetched by the browser, which runs on the
host and cannot resolve compose service names.

---

## Testing

```bash
cd backend && source venv/bin/activate && pytest      # 114 tests, ~5s
cd frontend && npx tsc --noEmit && npx eslint . && npm run build
```

The backend suite runs entirely against in-memory SQLite — no Postgres, no
Redis, no network. This is possible because `PortableVector` compiles to a
native `pgvector` column on PostgreSQL and a JSON-encoded `Text` column
elsewhere, so tests exercise the **real production ORM models** rather than a
parallel mock schema.

PostgreSQL-specific behavior that SQLite cannot express — RLS enforcement,
the `<=>` operator, full-text search — is verified directly against a live
instance.

Run the suite in the same Python version CI uses:

```bash
docker build -t sourceguard-backend ./backend
docker run --rm sourceguard-backend python -m pytest -q
```

---

## Ingestion dependencies

**No system-level packages required** — no `poppler`, no `tesseract`.

Layout parsing is built on PyMuPDF, a self-contained Python wheel.
[`unstructured`](https://github.com/Unstructured-IO/unstructured) offers
higher layout fidelity but requires system binaries and downloads
ONNX/detectron weights on first use, which would break two standing project
constraints: no local model downloads, and a fully offline dev/test path.

Tradeoff accepted: no OCR, so scanned PDFs are unsupported.

---

## CI

`.github/workflows/ci.yml` runs on push and pull request against `main`:

- **backend** — Python 3.11, pip-cached, `pytest`
- **frontend** — Node 22, npm-cached, `tsc --noEmit`, `eslint`, `next build`

No service containers needed: SQLite, faked Redis, and offline AI mocks mean
CI requires no Postgres, no Redis, and no network egress.

---

## Project structure

```
backend/
  app/
    api/          endpoints + dependencies (auth, RLS context, rate limiting)
    db/           async engine, session lifecycle, RLS bootstrap
    models/       SQLAlchemy models incl. PortableVector
    schemas/      Pydantic contracts
    services/     parsing, chunking, embeddings, retrieval, generation,
                  verification, conversation memory, telemetry
  tests/          114 tests
frontend/
  src/
    app/          App Router (dashboard route group + login)
    components/   ChatPanel, Sidebar, DocumentUpload, WorkspaceDocuments
    lib/          API client incl. hand-rolled SSE parser
infrastructure/   Terraform: ECS Fargate, ALB, VPC, IAM
```

---

## Deployment

**Primary:** [Render](https://render.com/) (backend, Docker) +
[Vercel](https://vercel.com/) (frontend), on managed Supabase and Upstash —
$0 fixed cost, automatic HTTPS on both, deploys on `git push`.

**Alternative:** `infrastructure/` contains validated Terraform for AWS ECS
Fargate behind an ALB, with a VPC, security-group chaining, and
least-privilege IAM. It is retained as the enterprise-scale migration target
(network isolation, auditable IAM, metric-driven autoscaling) rather than the
live deployment — roughly $57/month versus $0, which the current traffic does
not justify.

Full step-by-step for both paths: [`DEPLOYMENT.md`](DEPLOYMENT.md).

---

## Documentation

| Document | Contents |
| --- | --- |
| [`DESIGN.md`](DESIGN.md) | Full as-built architecture and the reasoning behind each non-obvious decision |
| [`WORKLOG.md`](WORKLOG.md) | Chronological build history across all twelve modules |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | Deployment: Render + Vercel (primary), with AWS ECS Fargate as an alternative |

---

## Status

All twelve modules across both phases are complete. 114 backend tests pass;
typecheck, lint, and build are clean; both Docker images build and run; the
Terraform configuration validates.

**Known deferred work** — documented rather than hidden:

- The Terraform validates but has never been applied to a live AWS account;
  on that path the ALB terminates HTTP only, so TLS requires an ACM
  certificate and a domain (steps in `DEPLOYMENT.md`). Not applicable to the
  primary Render/Vercel deployment, which provides HTTPS automatically.
- Document upload is synchronous; large files warrant a job queue
- Verification is lexical-overlap based, structured for replacement by a
  DeBERTa/NLI cross-encoder without touching decomposition or aggregation
- No OCR path for scanned PDFs
