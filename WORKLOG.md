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