# SourceGuard - System Design & Architecture (As-Built)

This document describes the system **as implemented** across Modules 1–4. For
the fixed engineering guidelines the codebase is held to, see `CLAUDE.md`.

## Technology Stack (As Implemented)
- **Language & Runtime:** Python 3.11+, FastAPI (async/await), Pydantic v2.
- **Frontend Stack:** Next.js 14+ (App Router), TypeScript, Tailwind CSS, Lucide React.
- **Database:** PostgreSQL with the `pgvector` extension, accessed via SQLAlchemy 2.0's async ORM (`asyncpg` driver).
- **In-Memory Cache:** Redis (Upstash) for semantic query caching and sliding-window rate-limiting.
- **AI Models:** Groq (chat generation) and Together AI (embeddings) via direct `httpx` calls to their OpenAI-compatible endpoints — both with deterministic, network-free mock fallbacks for local development and testing. No local model downloads (no torch/transformers): claim verification uses a dependency-free keyword-coverage heuristic, designed to be swapped for a real DeBERTa/cross-encoder model later without touching its decomposition/aggregation logic.

## Development Rules
1. **No Monolithic Files:** Code is modular by concern: routes (`app/api/`), services (`app/services/`), models (`app/models/`), schemas (`app/schemas/`).
2. **Explicit Error Handling:** Generic `Exception` is never swallowed silently; failures surface as structured `HTTPException` responses (400/404/409/422/502 as appropriate).
3. **Async First:** All database queries and external API calls are non-blocking (`asyncpg`, `httpx`, SQLAlchemy's async engine).
4. **Input Verification:** All incoming requests are validated using strict Pydantic schemas (`app/schemas/`).
5. **Security First:** No hardcoded API keys, database credentials, or JWT secrets — all configuration loads from `.env` via `app/core/config.py`.

---

## Module 1: Ingestion & Chunking (As-Built)

**Parsing — `app/services/document_parser.py` (`DocumentParser`)**
- PDF text extraction via **PyMuPDF (`fitz`)**: each page's `get_text()` output is joined with newlines; page count is returned alongside the full text.
- TXT files are decoded as UTF-8; a `UnicodeDecodeError` is converted into an `HTTPException(422)` rather than propagating a raw exception.
- Supported extensions are an explicit whitelist (`SUPPORTED_SUFFIXES = {".pdf": ..., ".txt": ...}`); anything else raises `HTTPException(400)`.

**Filename hardening — `DocumentParser._validate_filename()`**
- Runs *before* any extension check or parsing logic.
- Rejects a filename if `Path(filename).name != filename` (i.e. it carries directory components — relative traversal like `../../../etc/passwd.pdf` or an absolute path like `/etc/passwd.pdf`), or if the raw string contains a `".."` substring (this also catches Windows-style traversal such as `..\..\windows\system32\evil.pdf`, where backslashes aren't path separators on POSIX and wouldn't otherwise be stripped by `Path.name`).
- Any violation raises a strict `HTTPException(400)` — never a silent fallback or best-effort sanitization. Covered by dedicated regression tests (`tests/test_document_processing.py::TestDocumentParserSecurity`), including a disguised double-extension case (`invoice.pdf.exe`).

**Chunking — `app/services/chunker.py` (`RecursiveChunker`)**
- Wraps LangChain's `RecursiveCharacterTextSplitter` (`chunk_size`/`chunk_overlap` configurable, defaulting to 1000/200 characters; the constructor rejects `chunk_overlap >= chunk_size`).
- Recomputes each chunk's exact `start_offset`/`end_offset` in the source text via a forward-scanning `text.find()`, so downstream consumers can trace any chunk back to its exact source location.
- Tags every chunk with metadata: `chunk_size`, `chunk_overlap`, plus any caller-supplied `extra_metadata` (e.g. `filename`, `document_type`).

**Output contract — `app/schemas/document.py`**
- `DocumentParser.parse()` returns a `ParsingResult` (`document_id`, `filename`, `document_type`, `total_pages`, `total_chunks`, `chunks: list[DocumentChunk]`) — the single object consumed by both the test suite and the Module 4 upload endpoint.

---

## Module 2: Database & Vector Engine (As-Built)

**Async engine & session — `app/db/session.py`**
- `create_async_engine(settings.database_url, ...)` on the `asyncpg` driver, paired with `async_sessionmaker`.
- `get_db()` is the FastAPI dependency yielding one request-scoped `AsyncSession` per call.
- `app/db/init_db.py` provides an idempotent async bootstrap: `CREATE EXTENSION IF NOT EXISTS vector` followed by `Base.metadata.create_all`.

**Schema — `app/models/`**
- `Workspace` (1) → `Document` (many, `ondelete="CASCADE"`) → `DocumentChunk` (many, `ondelete="CASCADE"`); a standalone `AuditLog`.
- All primary/foreign keys use SQLAlchemy's dialect-portable `Uuid(as_uuid=True)` type (native `UUID` on Postgres, stored as text on SQLite).

**`PortableVector` — `app/models/types.py`**

The column type that lets `DocumentChunk.embedding` be a genuine `pgvector` column in production while remaining testable against an in-memory SQLite database, with no parallel "test model" required:

- It is a `TypeDecorator` whose declared `impl` is `Text`, but whose `comparator_factory` is set directly to `pgvector.sqlalchemy.Vector.Comparator`. This means `.cosine_distance()` / `.l2_distance()` / `.max_inner_product()` remain callable on the mapped `embedding` column for query *construction* regardless of which dialect ultimately executes the query.
- `load_dialect_impl(dialect)`: returns a real `Vector(1536)` (native `VECTOR(1536)`, ANN-indexable) when `dialect.name == "postgresql"`; returns plain `Text` for every other dialect.
- `process_bind_param` / `process_result_value`: pass the value through untouched on Postgres (pgvector's own driver-level serialization handles it), but `json.dumps`/`json.loads` the float list on any other dialect.
- Net effect: `tests/test_database.py` runs the *actual* production ORM models — not a mock schema — against `sqlite+aiosqlite:///:memory:`, while the deployed schema uses real `pgvector`.
- Chunk and audit `metadata` columns use the analogous, simpler pattern: `JSON().with_variant(JSONB(), "postgresql")` — native `JSONB` on Postgres, plain `JSON` elsewhere.
- `EMBEDDING_DIMENSIONS = 1536` is centralized as a fixed constant in `app/core/config.py` (imported by both `app/models/chunk.py` and `app/services/embeddings.py`) — deliberately *not* a `.env`-configurable `Settings` field, since changing it requires a schema migration, not a config edit.

---

## Module 3: Verification & Retrieval Core (As-Built)

**Embeddings — `app/services/embeddings.py` (`EmbeddingService`)**
- Live path: calls Together AI's OpenAI-compatible `/embeddings` endpoint (async, `httpx`) when `TOGETHER_API_KEY` is configured. Every returned vector's length is validated against `EMBEDDING_DIMENSIONS` (1536); a mismatch or transport failure raises `HTTPException(502)` rather than silently persisting a malformed vector.
- Mock path (`_mock_embedding`, used whenever no API key is set — the default for local dev/tests): **deterministic** — SHA-256 hash of the input text, first 4 bytes unpacked as a big-endian `uint32` seed, fed into `numpy.random.default_rng(seed).standard_normal(1536)`, then L2-normalized to unit length. Identical text always yields an identical vector; no network call, no model download.

**Hybrid Search & RRF — `app/services/retriever.py`**
- `build_vector_search_query(workspace_id, query_embedding, limit)` — a pure function producing `SELECT chunk.id, embedding <=> :query AS distance ... ORDER BY distance ASC`, scoped to a workspace via a join to `Document`. The `<=>` cosine-distance operator comes from `PortableVector`'s pgvector comparator.
- `build_keyword_search_query(workspace_id, query_text, limit)` — a pure function producing Postgres full-text search: `to_tsvector('english', content) @@ plainto_tsquery('english', :query)`, ordered by `ts_rank(...) DESC`.
- `reciprocal_rank_fusion(ranked_id_lists, k=60)` — merges any number of ranked ID lists using the standard RRF formula, `score(id) = Σ 1 / (k + rank)` (1-indexed rank) summed across every list the id appears in; returns `(id, score)` pairs sorted descending. A pure function with no DB/session dependency, so it's unit-tested directly against synthetic ID lists.
- `HybridRetriever.hybrid_search()` — orchestrates the two searches against the injected `AsyncSession` and fuses their ranked ID lists via `reciprocal_rank_fusion`, returning the top `top_k` `(chunk_id, score)` pairs. This *is* the hybrid search: dense pgvector ANN + sparse Postgres full-text ranking, merged by rank position (RRF) rather than by blending raw, differently-scaled distance/rank values.

**Claim verification — `app/services/nli_verifier.py` (`NLIVerifierService`)**
- A dependency-free heuristic verifier (no DeBERTa/cross-encoder download), built to be swapped for a real NLI model later without touching decomposition or aggregation logic.
- `decompose_claims()` splits a generated answer into sentence-level claims on a sentence-boundary lookbehind regex (`(?<=[.!?])\s+`).
- `_extract_keywords()` tokenizes to lowercase whole-word tokens (`\b[a-zA-Z0-9]+\b`) and strips a small stopword set.
- **Word-boundary enforcement (critical security/logic rule):** every keyword-to-chunk match in `_chunk_contains_keyword()` uses `re.search(rf"\b{re.escape(keyword)}\b", chunk_text, re.IGNORECASE)` — the `\b` boundaries are mandatory on both sides. This is what stops a claim keyword like `"cat"` from being falsely counted as present just because the source text contains `"category"` or `"concatenate"`. Covered by an explicit regression test (`test_word_boundary_prevents_partial_substring_match`).
- `verify_claim()` scores a claim as the fraction of its keywords found (whole-word) in its best-matching chunk, labeling it `ENTAILED` / `NOT_ENTAILED` / `INSUFFICIENT_EVIDENCE` against configurable thresholds (default 0.6 / 0.25). `verify_answer()` aggregates all claims into an `overall_score` and an `is_fully_supported` boolean.

---

## Module 4: API & Streaming Gateway (As-Built)

**Workspaces — `app/api/endpoints/workspaces.py`**
- `POST /api/v1/workspaces` creates a `Workspace`; a duplicate name raises `IntegrityError`, which is caught and converted to `HTTPException(409)`.

**Document upload — `app/api/endpoints/documents.py`**
- `POST /api/v1/documents/upload` accepts a `workspace_id` form field and one or more `UploadFile`s (`python-multipart`). Looks up the workspace first (`HTTPException(404)` if it doesn't exist), then per file: reads the raw bytes, calls `DocumentParser.parse()` — inheriting all of Module 1's path-traversal and extension validation for free — persists a `Document` row, batch-embeds every chunk's content via `EmbeddingService.embed_batch()` (Module 3), and inserts one `DocumentChunk` row per chunk with its embedding and metadata. Any single invalid file in a multi-file batch raises immediately (fail-fast `HTTPException`), so a request either fully succeeds or is rejected outright — never partially ingested.

**Generation — `app/services/generation.py` (`GenerationService`, new in Module 4)**
- The token-streaming counterpart to `EmbeddingService`, following the same live/mock design: when `GROQ_API_KEY` is set, it opens a streaming `POST` to Groq's OpenAI-compatible `/chat/completions` endpoint (`stream: true`), hand-parses the raw `data: {...}` SSE lines, and yields each token's `delta.content` as it arrives — wrapping any transport/parse failure in `HTTPException(502)`. With no key configured, `_mock_stream()` yields the top retrieved chunk's content word-by-word: deterministic, network-free. This service did not exist before Module 4 — none of Modules 1–3 produced generation tokens, and the streaming endpoint needed a concrete token source.

**Testable retrieval — `app/api/deps.py`**
- `get_hybrid_retriever()` is a FastAPI dependency that builds a `HybridRetriever` from the request's `AsyncSession` and a fresh `EmbeddingService`. Its purpose is testability: `hybrid_search()` issues Postgres-only SQL (pgvector `<=>`, `to_tsvector`) that a SQLite test database cannot execute at all. `tests/test_api.py` overrides exactly this dependency (`app.dependency_overrides[get_hybrid_retriever]`) with a stub returning canned `(chunk_id, score)` pairs, while chunk *content* is still read back from a real in-memory database via a portable `WHERE id IN (...)` query — so generation and verification are still exercised against genuine data end-to-end.

**Streaming endpoint — `app/api/endpoints/query.py`**
- `POST /api/v1/query/stream`, an `sse_starlette.EventSourceResponse`. Inside `_stream_query_events()`:
  1. `retriever.hybrid_search(workspace_id, query, top_k)` → ranked chunk IDs (Module 3).
  2. `_fetch_chunks_in_order()` — a portable `SELECT ... WHERE id IN (...)` — resolves IDs to chunk rows, preserving rank order; if none are found, emits `event: error` and stops.
  3. `GenerationService.stream_answer()` streams the answer; each token is emitted as `event: token`.
  4. Once the answer is complete, `NLIVerifierService.verify_answer()` scores every decomposed claim against the retrieved context; each is emitted as `event: verification`.
  5. A final `event: done` carries the full answer text, `overall_score`, and `is_fully_supported`.

**CORS — `app/main.py`**
- All three routers are wired via `app.include_router()`.
- `CORSMiddleware` enforces a strict policy: an explicit origin allow-list (`settings.cors_allowed_origins`, default `["http://localhost:3000"]`, overridable in `.env` as a comma-separated string via a `field_validator`), `allow_credentials=True`, and methods/headers restricted to exactly what the API needs (`GET`, `POST`, `OPTIONS`; `Content-Type`, `Authorization`) — no wildcard origins, methods, or headers.

---

## Frontend Architecture (As-Built)

**Framework & tooling**
- Next.js 14+ (App Router), TypeScript (strict mode), Tailwind CSS v4 (CSS-first config via `@theme inline` in `src/app/globals.css` — no `tailwind.config.js`), Lucide React for icons.
- Application code lives under `frontend/src/`: the CLI-generated `app/` directory was relocated to `src/app/` per Next.js's `src`-folder convention, since `src/app` is silently ignored whenever a root-level `app/` also exists. `tsconfig.json`'s `@/*` path alias resolves to `./src/*` accordingly.

**Utility layer — `src/lib/utils.ts`**
- `cn(...inputs: ClassValue[])` composes `clsx` (conditional class joining) with `tailwind-merge` (last-write-wins conflict resolution for Tailwind utilities — e.g. a later `w-16` correctly overrides an earlier `w-64` in the same class list rather than both being emitted). This is the single class-composition primitive used by every component with conditional or collapsed-state styling.

**Structural layout**
- `src/app/layout.tsx` (Root layout, Server Component): a fixed dark-mode-default shell — `zinc-950` background / `zinc-100` foreground applied directly, with no `prefers-color-scheme` branching or light theme. Renders the persistent `Sidebar` alongside a `min-w-0 flex-1` content region so routed pages fill the remaining width without overflow.
- `src/components/Sidebar.tsx` (Client Component — requires local collapse state): toggles between a `w-64` expanded rail and a `w-16` icon-only collapsed rail; contains a "New Workspace" affordance and a "Workspaces" list section rendering an explicit empty state (`No workspaces yet.`). The list is currently backed by a static placeholder array, not a live fetch — wiring it to `GET /api/v1/workspaces` (Module 4) is deferred to the next pass.
- `src/app/page.tsx` (Server Component): the dashboard shell, split via CSS grid into two panes — a Chat/Query pane (`minmax(0,1fr)`, disabled input, empty state) and a fixed-width (`360px`) Verification Audit Log pane. Both panes are structural placeholders anticipating the `POST /api/v1/query/stream` SSE contract (`event: token` / `event: verification` / `event: done`, per Module 4) without consuming it — no `EventSource`/`fetch` wiring exists in this pass.

**Type contracts — `src/types/index.ts`**

TypeScript interfaces are hand-mirrored from the backend's Python source of truth rather than generated, since no OpenAPI/codegen pipeline exists yet:
- `Workspace` ← `app/models/workspace.py::Workspace` — `id` / `name` / `created_at`, 1:1 with the SQLAlchemy model's mapped columns.
- `Document` ← `app/models/document.py::Document` — `document_type` narrowed to the `"pdf" | "txt"` literal union, mirroring `app/schemas/document.py::DocumentType`.
- `DocumentChunk` ← `app/models/chunk.py::DocumentChunk` — the ORM attribute is `chunk_metadata` (mapped to the `metadata` DB column); the TS field is named `metadata` to match what an eventual response schema would expose, since no Pydantic response schema for this model exists yet.
- `VerificationResult` / `ClaimVerification` ← `app/services/nli_verifier.py` dataclasses — field-for-field, including `EntailmentLabel` as the `"entailed" | "not_entailed" | "insufficient_evidence"` string union matching the Python `Enum`'s values.

**Current state**
- Purely structural: no `fetch`/`EventSource` calls exist anywhere in `frontend/src/` yet. `tsc --noEmit`, `eslint .`, and `next build` (Turbopack) all pass clean with zero warnings.
- Next integration pass: `Sidebar` → `GET /api/v1/workspaces`; dashboard query input → `POST /api/v1/query/stream`, consuming its SSE events into the Chat/Query and Verification Audit Log panes.

### Frontend Execution Roadmap (Planned)

**Module 5 — Workspace Architecture**
- `src/lib/api/workspaces.ts`: typed fetch wrappers — `listWorkspaces(): Promise<Workspace[]>` (`GET /api/v1/workspaces`) and `createWorkspace(name: string): Promise<Workspace>` (`POST /api/v1/workspaces`), surfacing the backend's `409` on duplicate names as a typed error rather than a generic throw.
- **Backend prerequisite:** `GET /api/v1/workspaces` does not exist yet — `app/api/endpoints/workspaces.py` (Module 4) currently implements only `POST`. Adding the list endpoint blocks this module.
- Active-workspace state: a minimal `WorkspaceProvider` context (or URL-driven `?workspace=<id>` state — exact mechanism TBD at implementation time) replacing `Sidebar`'s current static placeholder array and empty state.
- `Sidebar` wired to `listWorkspaces()` on mount, with the "New Workspace" button opening a form that calls `createWorkspace()` and re-fetches (or optimistically updates) the list.

**Module 6 — Document Ingestion UI**
- Upload surface (drag-and-drop + file picker) posting `multipart/form-data` to `POST /api/v1/documents/upload` (`workspace_id` + one or more files), matching `app/api/endpoints/documents.py`'s existing contract.
- Client-side error handling keyed to the backend's existing strict validation: `400` for disallowed extensions or path-traversal/disguised-extension filenames (`DocumentParser._validate_filename`), `404` for an unknown `workspace_id`, `422` for decode failures — each rendered as a distinct, actionable inline error rather than a generic failure toast, since the backend already fails fast and specifically per file.
- Per-file upload status list (`pending` / `uploading` / `parsed` / `rejected`) reflecting the backend's fail-fast batch behavior: one invalid file rejects the entire multi-file request, so the UI must communicate that a batch either fully succeeds or is fully rejected — never partial success.

**Module 7 — Streaming Interface & Real-Time Audit**
- Chat input wired to `POST /api/v1/query/stream` (`app/api/endpoints/query.py`). Since the native `EventSource` API only supports `GET`, this requires a `fetch` + `ReadableStream` SSE parser (manual `event:`/`data:` frame parsing) rather than `new EventSource(url)` — a real constraint worth fixing in the plan now, not discovering during implementation.
- Stream handling matches Module 4's four emitted event types exactly: `event: token` (append to the in-progress answer in the Chat/Query pane), `event: verification` (push a `ClaimVerification` row into the Verification Audit Log pane as it arrives — the "real-time" part), `event: done` (finalize the answer text, `overall_score`, `is_fully_supported`), `event: error` (empty-retrieved-context case).
- Audit Log entries color-coded by `EntailmentLabel` (`entailed` / `not_entailed` / `insufficient_evidence`, per `src/types/index.ts`), each linking back to its `supporting_chunk_index` so a claim can be traced to the exact source chunk that grounded it.
