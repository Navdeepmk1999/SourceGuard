# SourceGuard — System Design

This document describes the system **as implemented**. It records what was
built and, where a decision was non-obvious, why that option was chosen over
the alternatives.

For engineering rules see [`CLAUDE.md`](CLAUDE.md); for build history see
[`WORKLOG.md`](WORKLOG.md); for deployment see [`DEPLOYMENT.md`](DEPLOYMENT.md).

---

## 1. Data model

Five tenant-scoped tables, rooted at `workspaces`. Ownership flows outward
from `workspaces.user_id`; every RLS policy reaches back to it.

```
auth.users (Supabase)
     │  user_id (uuid, from the verified JWT `sub` claim)
     ▼
┌─────────────────┐
│ workspaces      │  id · user_id · name · created_at
└────────┬────────┘  UNIQUE(user_id, name)
         │ ON DELETE CASCADE
         ├──────────────────────────┐
         ▼                          ▼
┌─────────────────┐        ┌──────────────────┐
│ documents       │        │ chat_sessions    │
│ id              │        │ id               │
│ workspace_id FK │        │ workspace_id FK  │
│ filename        │        │ user_id          │
│ document_type   │        │ created_at       │
│ status          │        └────────┬─────────┘
│ error_message   │                 │ CASCADE
│ chunk_count     │                 ▼
│ created_at      │        ┌──────────────────┐
│ updated_at      │        │ chat_messages    │
└────────┬────────┘        │ id               │
         │ CASCADE         │ session_id FK    │
         ▼                 │ role             │
┌─────────────────┐        │ content          │
│ document_chunks │        │ claims  (JSONB)  │
│ id              │        │ created_at       │
│ document_id FK  │        └──────────────────┘
│ content         │
│ chunk_index     │
│ embedding       │  VECTOR(3072)
│ metadata (JSONB)│
│ created_at      │
└─────────────────┘
```

### Column notes

**`documents.status`** — `pending | processing | completed | failed`, a
CHECK-constrained text column rather than a Postgres `ENUM`. Adding a value
to an enum is a schema migration, and these states are the kind that grow.
Required because ingestion is asynchronous: the row must carry its own
progress, since the request that created it is long gone by the time the work
finishes.

**`document_chunks.embedding`** — `VECTOR(3072)`, the native width of
`gemini-embedding-001`. Narrower Matryoshka widths cannot be requested,
because Gemini's OpenAI-compatibility layer rejects the `dimensions` field
with a 400. The width is fixed at DDL time and `create_all` will not alter an
existing column, so changing providers means dropping and recreating the
table — which is why the constant lives in `config.py` and is deliberately
*not* environment-configurable.

> 3072 exceeds pgvector's 2000-dimension ceiling for `ivfflat` and `hnsw`
> indexes. No vector index is defined, so retrieval is a sequential scan.
> Acceptable at current corpus size; ANN indexing later would need `halfvec`.

**`chat_messages.claims`** — JSONB, nullable, no default:

```json
[{"claim": "Revenue reached 4.2M.", "label": "entailed",
  "score": 0.83, "supporting_chunk_index": 2}]
```

`NULL` means *no verdicts stored* — a user turn, or an assistant turn
predating the column. `[]` means *verified, and nothing was flagged*. The two
render differently in the UI, so the distinction is load-bearing: collapsing
them would relabel an unverified answer as clean.

**`chat_messages.created_at`** — a Python-side default with microsecond
precision, not `server_default=func.now()`. Conversation ordering depends on
this column, and SQLite's `now()` resolves only to the second, so sibling
messages written in one request would tie and replay out of order.

### Dialect portability

`PortableVector` is a SQLAlchemy `TypeDecorator` whose `impl` is `Text` but
whose `comparator_factory` is pgvector's. It compiles to a real
`VECTOR(3072)` on PostgreSQL and a JSON-encoded `Text` column elsewhere. That
single seam is why 169 backend tests run against in-memory SQLite using the
**real production ORM models** rather than a parallel mock schema.

---

## 2. Row-Level Security

### How a JWT becomes a tenant

```
1.  Browser         Supabase session → Authorization: Bearer <JWT>
                        │
2.  FastAPI         get_current_user()
                    Verifies ES256 signature against the project's JWKS
                    (PyJWKClient, cached). Fails closed: 401 on missing,
                    malformed, expired, or wrong-audience tokens.
                        │  user_id = JWT `sub`
3.  FastAPI         get_authenticated_db()
                    SELECT set_config('app.current_user_id', <user_id>, false)
                        │                                    └── session scope
4.  PostgreSQL      Policies evaluate app_current_tenant()
                        │
5.  FastAPI         Teardown: set_config('app.current_user_id', '', false)
                    before the connection returns to the pool
```

The tenant resolver, defined in `001_row_level_security.sql`:

```sql
CREATE OR REPLACE FUNCTION app_current_tenant()
RETURNS uuid LANGUAGE sql STABLE AS $$
  SELECT COALESCE(
    NULLIF(current_setting('app.current_user_id', true), '')::uuid,
    auth.uid()
  );
$$;
```

Three details in that function each earn their place:

- **It is not `auth.uid()` alone.** Supabase's documented RLS pattern works
  only when the caller reaches Postgres through PostgREST, which puts the end
  user's JWT on the database session. This backend verifies the JWT itself
  and connects as one long-lived role, so `auth.uid()` is NULL on every
  backend connection and an `auth.uid()`-only policy would deny every row.
- **`auth.uid()` is retained as a fallback**, so direct supabase-js access
  keeps working if the frontend ever queries these tables itself.
- **`NULLIF` is load-bearing.** `current_setting(name, true)` returns NULL
  when never set but an *empty string* once cleared, and `''::uuid` raises.
  Without it, a cleared context turns every query into an error instead of a
  clean zero-row result. It must fail closed, not fail loudly.

### The policies

| Table | Predicate |
| --- | --- |
| `workspaces` | `user_id = app_current_tenant()` |
| `documents` | `workspace_id IN (SELECT id FROM workspaces WHERE user_id = app_current_tenant())` |
| `document_chunks` | two hops, via `documents JOIN workspaces` |
| `chat_sessions` | `user_id = app_current_tenant()` |
| `chat_messages` | `session_id IN (SELECT id FROM chat_sessions WHERE user_id = app_current_tenant())` |

Each is `FOR ALL` with a matching `WITH CHECK`, so a caller cannot write a
row it would not be allowed to read. Every column the predicates filter on is
indexed; without that, each policy evaluation is a sequential scan on the
parent table.

### Why the connection role is the actual control

**PostgreSQL exempts `SUPERUSER` and `BYPASSRLS` roles from every policy,
unconditionally.** No table-level flag overrides this — `FORCE ROW LEVEL
SECURITY` extends policies to the table *owner*, not to a superuser. The
project's first implementation connected as `postgres`, so correct,
visible, audited policies enforced precisely nothing.

`init_db` therefore provisions a restricted `sourceguard_app` role:
`NOSUPERUSER NOBYPASSRLS`, CRUD grants only, never DDL, and deliberately not
the table owner. Two connection strings exist as a result — an admin URL for
bootstrap, and the runtime URL that the application actually uses.

### Session scope, and what it costs

The tenant variable is set with `set_config(..., false)` — **session** scope,
not transaction scope. Several endpoints commit mid-request, and a
transaction-scoped setting is discarded at each commit, so every subsequent
query would run with no tenant and correctly return nothing.

The consequence is a deployment constraint: a **transaction-mode** connection
pooler (Supabase port 6543) returns the backend connection to the pool
between transactions, which both breaks the context and risks handing a
backend carrying one tenant's variable to another client. Session-mode
pooling (port 5432) is mandatory. That trades connection-ceiling headroom for
correctness, which is the right direction — a connection limit is a capacity
problem you can measure; a tenant-isolation bug is unacceptable at any scale.

Background tasks need the same treatment for a different reason: they outlive
the request and therefore its session, so `tenant_session()` re-establishes
the context on its own connection. Without it, every insert from a background
task would be rejected by the policies meant to govern it.

### Defense in depth

RLS is the backstop, not the only check. `ensure_workspace_owner` and
`_load_owned_document` verify ownership in application code as well, because
RLS is inert on the SQLite test database and skippable by a misconfigured
role. Both return an identical **404** for "does not exist" and "belongs to
someone else", so IDs cannot be enumerated by comparing responses.

---

## 3. The verification pipeline

```
 ┌──────────────┐
 │  User query  │
 └──────┬───────┘
        ▼
 ┌──────────────────────────────────────────────┐
 │ 1. RETRIEVAL — hybrid                        │
 │    pgvector cosine ANN  +  Postgres FTS      │
 │    fused by Reciprocal Rank Fusion (k=60)    │
 └──────┬───────────────────────────────────────┘
        │  context_texts: list[str]
        ▼
 ┌──────────────────────────────────────────────┐
 │ 2. GENERATION — Groq, streamed               │
 │    context + role-attributed history         │
 │    tokens forwarded as SSE as they arrive    │
 └──────┬───────────────────────────────────────┘
        │  full_answer: str
        ▼
 ┌──────────────────────────────────────────────┐
 │ 3. CLAIM DECOMPOSITION                       │
 │    split on (?<=[.!?])\s+                    │
 └──────┬───────────────────────────────────────┘
        │  claims: list[str]
        ▼
 ┌──────────────────────────────────────────────┐
 │ 4. SCORING — per claim, against every chunk  │
 │    score = |matched keywords| / |keywords|   │
 │    best-matching chunk wins                  │
 │      ≥ 0.60  → entailed                      │
 │      ≤ 0.25  → insufficient_evidence         │
 │      else    → not_entailed                  │
 └──────┬───────────────────────────────────────┘
        │  ClaimVerification[]
        ▼
 ┌──────────────────────────────────────────────┐
 │ 5. PERSISTENCE — two-step write              │
 │    answer saved → verdicts attached (JSONB)  │
 └──────────────────────────────────────────────┘
```

### Retrieval: why Reciprocal Rank Fusion

Dense embeddings capture semantic similarity — "revenue decline" matches
"profits fell" — but miss rare exact tokens like error codes and SKUs. Sparse
full-text search is the mirror image. Hybrid covers both, but the two produce
**incomparable score scales**: cosine distance is bounded, `ts_rank` is
unbounded and corpus-relative. Any weighted blend needs a normalisation
constant that is arbitrary and drifts as the corpus grows.

RRF discards magnitude entirely and uses only rank position:

```
score(id) = Σ  1 / (k + rank_in_list)      k = 60
```

Scale-free by construction, and it has no tuning parameter that goes stale.

### Scoring: what it is, and what it is not

**This is a deterministic lexical heuristic, not a neural entailment model.**
The module is named `nli_verifier.py` and its vocabulary is NLI's
(`EntailmentLabel`, thresholds, per-claim verdicts), but the scoring function
is keyword coverage: the fraction of a claim's content words that appear as
whole words in the best-matching retrieved chunk.

That choice is defensible on its merits — deterministic, explainable, zero
model dependencies, no download, and fully exercisable offline, which is why
the entire test suite runs without network access. It is also genuinely
limited: **it cannot detect negation** ("the contract does *not* expire"
scores identically to the affirmative) **or pure paraphrase**.

The critical implementation detail is word-boundary matching. Every keyword
test uses `\b{keyword}\b`. Without it, "cat" counts as supported by source
text containing "category" or "concatenate" — a false `entailed`, which is
exactly the failure this product exists to prevent. A dedicated regression
test guards it.

The module is structured so `_score_claim_against_chunk` can be replaced by a
DeBERTa/NLI cross-encoder **without touching** decomposition, aggregation,
thresholds, the SSE contract, or the UI. That is the intended upgrade path.

### Aggregation

```python
overall_score      = round(mean(claim.score for claim in claims), 4)
is_fully_supported = all(claim.label == ENTAILED for claim in claims)
```

Both are **derived on read**, never stored. `aggregate_claims()` is the single
definition, shared by live verification and by history replay — a second copy
would drift and make a restored answer score differently from the one the
user originally saw.

### Persistence, and the two-step write

The assistant turn is saved the moment streaming ends; verdicts are attached
in a **second** write afterwards. Folding both into one write would mean a
verifier failure costs the response itself rather than just its audit trail.

Historical rows are deliberately not backfilled. Verdicts cannot be
reconstructed after the fact: re-running the verifier would score old answers
against *today's* retrieved chunks, not the chunks those answers came from. A
fabricated audit trail is worse than an absent one, so those rows keep `NULL`
and the UI states that verdicts are unavailable.

### Streaming contract

| Event | Payload | When |
| --- | --- | --- |
| `session` | `{session_id}` | First, before any token |
| `token` | `{token}` | Per generated token |
| `verification` | `{claim, label, score}` | Per claim, after generation |
| `done` | `{answer, session_id, overall_score, is_fully_supported}` | Last |
| `error` | `{detail}` | Terminal failure |

`session` is emitted first so a client starting a new conversation learns its
id even if the stream later errors — it can still continue the thread.

The frontend hand-parses SSE off `fetch` + `ReadableStream`, because the
browser's native `EventSource` is **GET-only** and this endpoint is a POST
with a JSON body. The parser strips the trailing `\r` from `sse-starlette`'s
CRLF-terminated frames, and returns `null` for unrecognised events rather
than throwing, so one malformed frame cannot abort a good stream.

---

## 4. Asynchronous ingestion

```
POST /documents/upload
   │  validate filename + extension synchronously  →  400 on failure
   │  INSERT document (status='pending')
   │  COMMIT                       ← before scheduling; a task against an
   │                                 uncommitted row finds nothing to update
   └─ 202 Accepted {document_id, status_url}
            │
            ▼  BackgroundTask (in-process)
      tenant_session(user_id)      ← re-establishes RLS context
            │
      status='processing'
            │
      parse → chunk → embed (paced 15/min) → INSERT chunks
            │
      status='completed', chunk_count=N        [or 'failed' + error_message]
```

**Zero chunks is a failure.** A PDF with no text layer previously committed
as `completed` with `chunk_count=0`: the upload looked successful and the
document was silently absent from every answer. It now fails with an
actionable message naming OCR as the likely need.

**Every status write is verified.** An `UPDATE` matching no rows commits
happily in SQL, so a document deleted mid-ingestion — or one filtered out by
RLS because the tenant context went missing — would have been reported as
ingested while writing nothing. The rowcount is checked and a mismatch raises.

**Failures are recorded in a fresh session.** Whatever failed may have left
the original transaction aborted, where every further statement raises; the
failure write is the only channel the user can still observe.

---

## 5. Rate limiting

Two independent mechanisms, often confused:

**Inbound (per user).** A sliding-window log in a Redis sorted set, executed
as a single atomic Lua script — `ZREMRANGEBYSCORE`, `ZCARD`, limit check,
`ZADD`, `EXPIRE`. Atomicity matters: a Python-side check-then-write lets two
concurrent requests both read `count = 9` and both proceed. Keyed by
authenticated `user_id`, not IP, so it cannot be sidestepped by rotating
addresses. **Fails open** on `RedisError` — a Redis outage should not take
the API down with it.

**Outbound (to the embedding provider).** Three cooperating parts:

| Mechanism | Value | Purpose |
| --- | --- | --- |
| Pacer | 4.0 s between request *starts* | Bounds the rate to 15/min |
| Semaphore | 2 concurrent | Bounds in-flight connections |
| Backoff | 2 s × 2ⁿ, 5 retries, proportional jitter | Absorbs 429s |

The pacer is what actually bounds the rate. A per-task `sleep(2)` with two
workers still yields ~60 requests/minute, because the workers sleep in
parallel — only spacing the *starts* holds an average. `Retry-After` is
honoured when present. Only 429 is retried; a 400 is deterministic and
retrying it burns quota while delaying an error the caller needs.
