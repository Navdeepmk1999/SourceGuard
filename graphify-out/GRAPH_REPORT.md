# Graph Report - SourceGuard  (2026-08-28)

## Corpus Check
- Corpus is ~12,528 words - fits in a single context window. You may not need a graph.

## Summary
- 455 nodes · 781 edges · 24 communities (19 shown, 5 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 53 edges (avg confidence: 0.87)
- Token cost: 9,906 input · 21,807 output

## Community Hubs (Navigation)
- Frontend UI & Design Rationale
- Settings, App Entry & Embeddings
- Document Upload & Schemas
- ORM Models & Portable Vector
- Frontend Package Dependencies
- Claim Verification (NLI)
- Hybrid Retrieval & RRF
- Query Streaming & DB Session
- PDF/TXT Parsing & Security Tests
- TypeScript Compiler Config
- API Integration Tests
- Workspace Routes & Schemas
- ESLint Config
- Next.js Config
- PostCSS Config
- Alembic Migration Dependency
- Uvicorn Server Dependency

## God Nodes (most connected - your core abstractions)
1. `EmbeddingService` - 27 edges
2. `NLIVerifierService` - 26 edges
3. `DocumentParser` - 24 edges
4. `DocumentChunk` - 18 edges
5. `RecursiveChunker` - 17 edges
6. `HybridRetriever` - 17 edges
7. `compilerOptions` - 16 edges
8. `upload_documents()` - 15 edges
9. `GenerationService` - 15 edges
10. `Settings` - 14 edges

## Surprising Connections (you probably didn't know these)
- `Rule: Explicit Error Handling` --semantically_similar_to--> `ApiError`  [INFERRED] [semantically similar]
  CLAUDE.md → frontend/src/lib/api.ts
- `Chunk-Size Drift: 800/150 logged vs 1000/200 as-built` --references--> `RecursiveChunker`  [AMBIGUOUS]
  WORKLOG.md → backend/app/services/chunker.py
- `localhost and 127.0.0.1 Are Distinct Origins` --rationale_for--> `request()`  [INFERRED]
  DESIGN.md → frontend/src/lib/api.ts
- `Rule: Async First` --rationale_for--> `get_db()`  [INFERRED]
  CLAUDE.md → backend/app/db/session.py
- `Dependency-Override Testability Seam` --rationale_for--> `get_hybrid_retriever()`  [EXTRACTED]
  DESIGN.md → backend/app/api/deps.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **SSE Query Flow (retrieve to verified answer)** — backend_app_api_endpoints_query_stream_query_events, backend_app_services_retriever_hybridretriever_hybrid_search, backend_app_api_endpoints_query_fetch_chunks_in_order, backend_app_services_generation_generationservice_stream_answer, backend_app_services_nli_verifier_nliverifierservice_verify_answer, design_sse_streaming_contract [EXTRACTED 1.00]
- **Keyless Offline Development Path** — backend_app_services_embeddings_embeddingservice, backend_app_services_generation_generationservice, backend_app_services_nli_verifier_nliverifierservice, backend_app_models_types_portablevector, design_deterministic_mock_fallback, design_no_local_models [INFERRED 0.95]
- **Module 5 Workspace Vertical Slice** — backend_app_api_endpoints_workspaces_list_workspaces, frontend_src_lib_api_getworkspaces, frontend_src_context_workspacecontext_workspaceprovider, frontend_src_components_sidebar_sidebar, frontend_src_app_page_home [EXTRACTED 1.00]

## Communities (24 total, 5 thin omitted)

### Community 0 - "Frontend UI & Design Rationale"
Cohesion: 0.05
Nodes (51): Rule: Explicit Error Handling, addWorkspace Resolves null Instead of Throwing, ApiError status 0 for Unreachable Backend, cn() Class-Composition Primitive, Fixed Dark-Mode-Default Shell, Fail-Fast Multi-File Upload, Hand-Mirrored TypeScript Contracts, useWorkspaces() Throws Outside Its Provider (+43 more)

### Community 1 - "Settings, App Entry & Embeddings"
Cohesion: 0.06
Nodes (30): get_settings(), Application configuration loaded from environment variables / .env., Settings, health_check(), get, Liveness probe used by orchestration/monitoring., EmbeddingService, AsyncClient (+22 more)

### Community 2 - "Document Upload & Schemas"
Cohesion: 0.07
Nodes (33): AsyncSession, post, UUID, upload_documents(), DocumentChunk, DocumentIngestSummary, DocumentType, DocumentUpload (+25 more)

### Community 3 - "ORM Models & Portable Vector"
Cohesion: 0.10
Nodes (23): AuditLog, Immutable record of actions taken against workspace entities., Base, DocumentChunk, A chunk of a Document, holding its text, embedding vector, and metadata., Document, A single ingested file (PDF/TXT) belonging to a Workspace., PortableVector (+15 more)

### Community 4 - "Frontend Package Dependencies"
Cohesion: 0.05
Nodes (38): clsx, eslint, eslint-config-next, dependencies, clsx, lucide-react, next, react (+30 more)

### Community 5 - "Claim Verification (NLI)"
Cohesion: 0.09
Nodes (17): _chunk_contains_keyword(), ClaimVerification, EntailmentLabel, NLIVerifierService, Enum, str, True if `keyword` appears in `chunk_text` as a whole word. CRITICAL: word…, Decomposes a generated answer into claims and scores each claim's entailment… (+9 more)

### Community 6 - "Hybrid Retrieval & RRF"
Cohesion: 0.12
Nodes (16): build_keyword_search_query(), build_vector_search_query(), HybridRetriever, UUID, ANN query ranking chunks by pgvector cosine distance, scoped to a workspace., PostgreSQL full-text search query ranking chunks by ts_rank against a tsquery., Merges ranked ID lists via RRF: score(id) = sum(1 / (k + rank)) across every…, Combines pgvector cosine-similarity search with PostgreSQL full-text search,… (+8 more)

### Community 7 - "Query Streaming & DB Session"
Cohesion: 0.10
Nodes (28): get_hybrid_retriever(), AsyncSession, Overridable in tests so callers don't need a live Postgres/pgvector instance., _fetch_chunks_in_order(), AsyncSession, post, UUID, Fetches chunks by primary key and returns them in `chunk_ids` order. Uses a… (+20 more)

### Community 8 - "PDF/TXT Parsing & Security Tests"
Cohesion: 0.11
Nodes (15): DocumentParser, Parses PDF and TXT documents into raw text, then delegates chunking to…, Extract text from raw PDF bytes. Returns (full_text, page_count)., Decode raw TXT bytes into text., Reject path traversal / directory components. Returns the bare filename., Parse a document (by filename extension) and chunk it into a ParsingResult., PyMuPDF (fitz), make_pdf_bytes() (+7 more)

### Community 9 - "TypeScript Compiler Config"
Cohesion: 0.07
Nodes (28): compilerOptions, allowJs, esModuleInterop, incremental, isolatedModules, jsx, lib, module (+20 more)

### Community 10 - "API Integration Tests"
Cohesion: 0.11
Nodes (11): pytest, client(), db_engine(), fixture, UUID, Bypasses the Postgres-only hybrid search SQL (pgvector cosine distance, full-…, session_maker(), _StubRetriever (+3 more)

### Community 11 - "Workspace Routes & Schemas"
Cohesion: 0.16
Nodes (15): create_workspace(), list_workspaces(), AsyncSession, get, post, Returns every workspace, newest first., BaseModel, Workspace as returned by the API. (+7 more)

## Ambiguous Edges - Review These
- `RecursiveChunker` → `Chunk-Size Drift: 800/150 logged vs 1000/200 as-built`  [AMBIGUOUS]
  WORKLOG.md · relation: references
- `page.tsx` → `Unmodified create-next-app README`  [AMBIGUOUS]
  frontend/README.md · relation: references

## Knowledge Gaps
- **73 isolated node(s):** `eslintConfig`, `nextConfig`, `name`, `version`, `private` (+68 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **5 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `RecursiveChunker` and `Chunk-Size Drift: 800/150 logged vs 1000/200 as-built`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **What is the exact relationship between `page.tsx` and `Unmodified create-next-app README`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **Why does `DocumentParser` connect `PDF/TXT Parsing & Security Tests` to `Document Upload & Schemas`?**
  _High betweenness centrality (0.100) - this node is a cross-community bridge._
- **Why does `EmbeddingService` connect `Settings, App Entry & Embeddings` to `Document Upload & Schemas`, `Hybrid Retrieval & RRF`, `Query Streaming & DB Session`?**
  _High betweenness centrality (0.096) - this node is a cross-community bridge._
- **Why does `NLIVerifierService` connect `Claim Verification (NLI)` to `Hybrid Retrieval & RRF`, `Query Streaming & DB Session`?**
  _High betweenness centrality (0.078) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `EmbeddingService` (e.g. with `upload_documents()` and `Settings`) actually correct?**
  _`EmbeddingService` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `NLIVerifierService` (e.g. with `TestClaimDecomposition` and `TestNLIEntailmentScoring`) actually correct?**
  _`NLIVerifierService` has 2 INFERRED edges - model-reasoned connections that need verification._