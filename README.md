# SourceGuard

Retrieval-augmented answers, verified against source. Every claim in a
generated answer is decomposed and checked against the retrieved context
before it reaches the user.

For the full as-built architecture see [`DESIGN.md`](DESIGN.md); for the
build history see [`WORKLOG.md`](WORKLOG.md).

## Stack

- **Backend:** Python 3.11+, FastAPI (async), Pydantic v2, SQLAlchemy 2.0
- **Database:** PostgreSQL + `pgvector` (hybrid dense/sparse retrieval via RRF)
- **Cache / rate limiting:** Redis (sliding-window, per user)
- **Auth:** Supabase (ES256 JWTs verified via JWKS)
- **Frontend:** Next.js (App Router), TypeScript, Tailwind CSS

## Prerequisites

- Python 3.11+
- Node.js 20+
- Docker (for local Postgres + Redis)

```bash
# Postgres with pgvector, and Redis
docker run -d --name sourceguard-db -p 5432:5432 \
  -e POSTGRES_PASSWORD=postgres pgvector/pgvector:pg16
docker run -d --name sourceguard-redis -p 6379:6379 redis:7-alpine
```

## Setup

```bash
# Backend
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# Create backend/.env with the variables in the table below
# Bootstrap: creates tables, the pgvector extension, the restricted
# application role, and the RLS policies. Needs the ADMIN connection.
ADMIN_DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/sourceguard" \
  APP_DB_PASSWORD="choose-a-password" python -m app.db.init_db
# Then point DATABASE_URL at the restricted role it just created:
#   postgresql+asyncpg://sourceguard_app:<that password>@localhost:5432/sourceguard
uvicorn app.main:app --reload

# Frontend
cd frontend
npm install
npm run dev                   # http://localhost:3000
```

Reach the app at `http://localhost:3000`, **not** `http://127.0.0.1:3000` —
the backend's CORS allow-list contains the former only, and the two are
distinct origins to the browser.

### Environment

`backend/.env`:

| Variable | Required | Notes |
| --- | --- | --- |
| `DATABASE_URL` | yes | `postgresql+asyncpg://...`. **Must be the restricted, non-superuser role** (see below) — a superuser bypasses Row-Level Security entirely. |
| `ADMIN_DATABASE_URL` | for bootstrap | Superuser connection, used only by `app.db.init_db` for DDL. Falls back to `DATABASE_URL`. |
| `APP_DB_PASSWORD` | for bootstrap | Password `init_db` assigns to the restricted role it creates. |
| `APP_DB_ROLE` | no | Restricted role name (default `sourceguard_app`). |
| `SUPABASE_URL` | yes | Project URL; used to build the JWKS endpoint. Not a secret. |
| `REDIS_URL` | no | Defaults to `redis://localhost:6379/0`. |
| `GROQ_API_KEY` | no | Unset ⇒ deterministic offline mock generation. |
| `TOGETHER_API_KEY` | no | Unset ⇒ deterministic offline mock embeddings. |
| `LANGSMITH_API_KEY` | no | Required for tracing; see below. |
| `LANGCHAIN_TRACING_V2` / `LANGSMITH_TRACING` | no | `true` enables tracing (needs the key above). |

`frontend/.env.local`: `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_SUPABASE_URL`,
`NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`.

With no AI provider keys set, embeddings and generation fall back to
deterministic, network-free mocks — the whole pipeline runs offline.

## Document ingestion dependencies

**No system-level packages are required.** Ingestion uses layout-aware
parsing built on **PyMuPDF**, a self-contained Python wheel.

This is deliberate. The obvious alternative, [`unstructured`](https://github.com/Unstructured-IO/unstructured),
gives excellent layout partitioning but its high-fidelity PDF path wants
system `poppler` and `tesseract`, and its layout-detection models download
ONNX/detectron weights on first use. That would break two standing
constraints of this project: no local model downloads, and a fully offline
dev/test path. PyMuPDF's `find_tables()` and per-span font metadata cover
what the pipeline actually needs.

So there is **no `poppler`/`tesseract` install step, and no fallback path to
document** — `pip install -r requirements.txt` is sufficient on macOS,
Linux, and Windows.

What the parser extracts (see `app/services/layout_parser.py`):

- **Headings** — detected by font size relative to the document's own body
  text, plus short bold lines and Markdown `#` prefixes
- **Paragraphs** and **list items** (`-`, `•`, `1.`, `a)` …)
- **Tables** — via `find_tables()`, rendered to Markdown so row/column
  relationships survive into the embedding

Chunking (`app/services/semantic_chunker.py`) then splits on those
boundaries rather than character counts: tables stay atomic, headings bind
to the content they introduce, and splits land between elements rather than
mid-sentence.

## Running with Docker

Postgres and Redis are **not** part of `docker-compose.yml` — the local setup
already runs them as standalone containers on the standard ports, and
declaring them again would collide on 5432/6379. Start them first (see
[Prerequisites](#prerequisites)), then:

```bash
# From the repo root
docker compose up --build
```

- Frontend → http://localhost:3000
- Backend → http://localhost:8000 (health at `/health`)

The backend waits for its own healthcheck before the frontend starts
(`depends_on: condition: service_healthy`).

### Configuration

The backend inherits `backend/.env` via `env_file`, so Supabase, Groq,
Together, and LangSmith settings carry over unchanged. Two values are
overridden in compose because they differ inside a container:

| Variable | Why it's overridden |
| --- | --- |
| `DATABASE_URL` | `localhost` inside a container is the container itself, not the host running Postgres — rewritten to `host.docker.internal`. |
| `REDIS_URL` | Same reason. |

Override either by exporting it (or putting it in a root `.env`) before
`docker compose up` — e.g. to point at managed Supabase/Upstash instances.

**`NEXT_PUBLIC_*` are build-time, not runtime.** Next.js inlines them into
the client bundle during `npm run build`, so they are passed as **build
args**, not environment variables. Setting them only at `docker run` time
silently produces a bundle with `undefined` baked in.

```bash
export NEXT_PUBLIC_SUPABASE_URL=https://<ref>.supabase.co
export NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=<publishable-key>
docker compose up --build     # values are read into the frontend build args
```

Note that `NEXT_PUBLIC_API_URL` defaults to `http://localhost:8000/api/v1`,
**not** `http://backend:8000`. That URL is fetched by the user's browser,
which runs on the host and cannot resolve compose service names; `backend`
resolves only between containers.

### Building images individually

```bash
docker build -t sourceguard-backend ./backend

docker build -t sourceguard-frontend ./frontend \
  --build-arg NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1 \
  --build-arg NEXT_PUBLIC_SUPABASE_URL=https://<ref>.supabase.co \
  --build-arg NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=<publishable-key>
```

Both images run as non-root. The frontend uses Next.js standalone output, so
the runtime stage ships only `server.js` plus the modules it actually needs
(`public/` and `.next/static/` are copied in explicitly — standalone omits
them by design).

You can run the test suite in the same Python version CI uses:

```bash
docker build -t sourceguard-backend ./backend
docker run --rm sourceguard-backend python -m pytest -q
```

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request against
`main`:

- **backend** — Python 3.11, `pip install -r requirements.txt`, `pytest`
- **frontend** — Node 22, `npm ci`, `tsc --noEmit`, `eslint`, `next build`

No service containers are needed: the backend suite runs against in-memory
SQLite with Redis faked, and with no AI provider keys set the embedding and
generation services fall back to their deterministic offline mocks — so CI
requires no Postgres, no Redis, and no network egress.

## Tests

```bash
cd backend && source venv/bin/activate && pytest      # 114 tests
cd frontend && npx tsc --noEmit && npx eslint . && npm run build
```

The backend suite runs entirely against in-memory SQLite with no Postgres,
Redis, or network access required.

## Security note

Row-Level Security is **enforced** on `workspaces`, `documents`,
`document_chunks`, `chat_sessions`, and `chat_messages`. The application
connects as a restricted role (`sourceguard_app`) that `init_db` provisions
with `NOSUPERUSER NOBYPASSRLS`, so Postgres applies every policy — a
superuser connection would bypass them all regardless of how the policies
are written.

If you point `DATABASE_URL` at a superuser, isolation silently reverts to
application-layer checks only. Verify with:

```sql
SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user;
-- both must be false
```
