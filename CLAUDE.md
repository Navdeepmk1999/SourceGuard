# SourceGuard - AI Coding Guidelines & System Directives

## Code & Architecture Standards
- **Language & Runtime:** Python 3.11+, FastAPI (async/await), Pydantic v2.
- **Frontend Stack:** Next.js 14+ (App Router), TypeScript, Tailwind CSS, Lucide React.
- **Database:** PostgreSQL with `pgvector` extension for vector storage and relational metadata.
- **In-Memory Cache:** Redis (Upstash) for semantic query caching and sliding-window rate-limiting.
- **AI Models:** Groq / Together AI (Fast inference via open models), HuggingFace Cross-Encoders for reranking, DeBERTa-v3/NLI for claim verification.

## Development Rules
1. **No Monolithic Files:** Keep code modular. Separate routes (`app/api/`), services (`app/services/`), models (`app/models/`), and schemas (`app/schemas/`).
2. **Explicit Error Handling:** Never catch generic `Exception` without re-raising or returning structured JSON HTTP errors (`HTTPException`).
3. **Async First:** All database queries and external API calls must be non-blocking using `asyncio` or async libraries (`asyncpg`, `httpx`).
4. **Input Verification:** All incoming requests must be validated using strict Pydantic schemas.
5. **Security First:** Never hardcode API keys, database credentials, or JWT secrets. Always load from `.env`.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
