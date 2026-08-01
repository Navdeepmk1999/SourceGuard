# SourceGuard Engineering Worklog

## Day 1 - Repository Setup & System Architecture
- Configured isolated SSH keys and folder-specific Git settings for `Navdeepmk1999`.
- Initialized core repository structures and authored system directives (`CLAUDE.md`) and system specifications (`DESIGN.md`).
- Designed multi-tenant PostgreSQL schema supporting `pgvector` extension and hybrid search indexing (HNSW + Full-Text Search).

## Day 1 (Continued) - Module 1 Implementation
- Initialized FastAPI backend and created modular service architecture (`app/services`, `app/schemas`).
- Implemented `document_parser.py` using PyMuPDF to extract text from PDFs and TXT files.
- Built `chunker.py` using recursive text splitting (800 token chunks, 150 overlap) with precise metadata offset tagging.
- Detected and mitigated a localized homograph attack (`πthon`) within the virtual environment bin directory.
- Achieved 100% pass rate across 10 unit tests covering ingestion, chunking limits, and error paths.
- Hardened `document_parser.py` against path-traversal and disguised-extension filenames, rejecting them with a strict HTTP 400 instead of silently degrading; covered by 6 new security tests (16 passing total).

## Day 2 - Module 2 Implementation (Database & Vector Engine)
- Wired an async SQLAlchemy engine/session layer (`app/db/session.py`) on `asyncpg`, plus `app/db/init_db.py` to bootstrap the `pgvector` extension and create all tables.
- Modeled the core relational schema in `app/models/`: `Workspace` → `Document` → `DocumentChunk` (cascade deletes) and a standalone `AuditLog`.
- Added a portable embedding column type (`PortableVector`, 1536 dimensions) that compiles to native `pgvector.Vector` on Postgres and a JSON-encoded fallback on SQLite, plus a JSON/JSONB variant for chunk and audit metadata, so the same ORM models are exercisable against a mock SQLite DB in tests without a live Postgres instance.
- Confirmed `DATABASE_URL` loads correctly from `.env` regardless of working directory.
- Achieved 100% pass rate across 12 new async database tests (relationships, cascade deletes, joins, uniqueness constraints) — 22 tests passing overall.

## Day 3 - Module 3 Implementation (Verification & Retrieval Core)
- Built `embeddings.py`: an async embedding service targeting Together AI's OpenAI-compatible endpoint, with dimension validation (1536) on live responses and a deterministic, unit-normalized mock generator (SHA-256-seeded) for offline dev/tests — no torch/transformers download required.
- Built `retriever.py`: hybrid search combining pgvector cosine-distance ANN search with PostgreSQL full-text search (`tsvector`/`plainto_tsquery`/`ts_rank`), merged via Reciprocal Rank Fusion; query builders are pure functions so SQL generation is unit-testable without a live database.
- Extended `PortableVector` with pgvector's comparator so `.cosine_distance()` is usable directly on the ORM column for query construction.
- Built `nli_verifier.py`: decomposes a generated answer into sentence-level claims and scores entailment against source chunks via keyword coverage; strictly enforces regex word-boundaries (`\b`) on every keyword match to prevent partial substring false-positives (e.g. "cat" incorrectly matching inside "category") per the project's regex security rule.
- Achieved 100% pass rate across 26 new tests (RRF merge logic, hybrid query generation, embedding mock behavior, claim decomposition, entailment scoring, and the word-boundary regression case) — 48 tests passing overall.

## Day 4 - Module 4 Implementation (API & Streaming Gateway)
- Added `app/api/endpoints/`: `workspaces.py` (workspace creation, 409 on duplicate names), `documents.py` (multi-file upload wired to Module 1's `DocumentParser` + Module 3's `EmbeddingService`, 404 on unknown workspace), and `query.py` (SSE endpoint streaming answer tokens then NLI verification scores).
- Added `generation.py`: a small Groq-backed streaming token service (live SSE parsing when `GROQ_API_KEY` is set, deterministic mock stream otherwise) — needed to produce the "LLM generation tokens" the streaming endpoint yields, since no generation service existed yet from prior modules.
- Introduced `app/api/deps.py` with an overridable `get_hybrid_retriever` dependency so `/query/stream` can be integration-tested without a live Postgres/pgvector instance — the retriever is swapped for a stub in tests while chunk content is still fetched from a real (in-memory) DB via a portable `WHERE id IN (...)` query.
- Wired all routers into `app/main.py` behind a strict `CORSMiddleware` policy (explicit allow-listed origins from `.env`, restricted methods/headers, no wildcard).
- Achieved 100% pass rate across 10 new integration tests (workspace creation/conflict, upload success/invalid-extension/path-traversal/unknown-workspace rejection, SSE event structure, empty-context handling) — 58 tests passing overall.