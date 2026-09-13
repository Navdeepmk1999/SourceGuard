import asyncio
import hashlib
import logging
import random
import struct

import httpx
import numpy as np
from fastapi import HTTPException

from app.core.config import EMBEDDING_DIMENSIONS, Settings, get_settings

logger = logging.getLogger(__name__)

# Gemini requires one text per request, so a 500-chunk document becomes 500
# calls. The free tier allows 15 requests/minute, and exceeding it returns 429.
#
# Two mechanisms keep us under it, because concurrency alone cannot:
#   * the semaphore caps how many requests are in flight at once;
#   * the pacer enforces a minimum gap between request *starts*.
#
# The pacer is what actually bounds the rate. A per-task `sleep(2)` with two
# workers would still yield roughly 60 requests/minute - four times the limit -
# because the two workers sleep in parallel. Spacing starts 60/15 = 4s apart is
# what holds the average at 15/min regardless of how long each call takes.
_MAX_CONCURRENT_EMBEDDING_REQUESTS = 2
_EMBEDDING_REQUESTS_PER_MINUTE = 15
_MIN_REQUEST_INTERVAL_SECONDS = 60 / _EMBEDDING_REQUESTS_PER_MINUTE

# Retries apply ONLY to 429. Other 4xx are deterministic and would fail again.
_MAX_RATE_LIMIT_RETRIES = 5
_BASE_RETRY_DELAY_SECONDS = 2.0


class _RequestPacer:
    """Spaces request starts by at least `interval` seconds.

    Holding the lock across the sleep is deliberate: it serializes the
    reservation so concurrent callers queue up at 0s, 4s, 8s rather than all
    reading the same timestamp and starting together.

    Scope is this object, so pacing is per-EmbeddingService-instance and
    per-process. Concurrent uploads that each build their own service can
    therefore still exceed the quota; a cluster-wide limit would need shared
    state, as the API rate limiter does with Redis.
    """

    def __init__(self, interval: float) -> None:
        self._interval = interval
        self._lock = asyncio.Lock()
        self._next_allowed: float | None = None

    async def wait(self) -> None:
        if self._interval <= 0:
            return
        async with self._lock:
            loop = asyncio.get_running_loop()
            if self._next_allowed is not None:
                delay = self._next_allowed - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
            self._next_allowed = loop.time() + self._interval


class EmbeddingService:
    """Generates `EMBEDDING_DIMENSIONS`-dim embeddings via an external, OpenAI-
    compatible embeddings API (Together AI by default). Falls back to a
    deterministic mock embedding generator when no API key is configured, so
    the service works for local development and tests without network access."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
        min_request_interval: float | None = None,
        base_retry_delay: float | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None
        # Overridable so tests exercise the pacing and retry paths without
        # spending real wall-clock seconds on them.
        self._base_retry_delay = (
            _BASE_RETRY_DELAY_SECONDS if base_retry_delay is None else base_retry_delay
        )
        self._pacer = _RequestPacer(
            _MIN_REQUEST_INTERVAL_SECONDS if min_request_interval is None else min_request_interval
        )

    @property
    def is_live(self) -> bool:
        return bool(self._settings.together_api_key)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._settings.together_api_base, timeout=30.0)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def embed(self, text: str) -> list[float]:
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embeds each text in its own request, concurrently.

        Gemini's OpenAI-compatibility layer returns 400 Bad Request when
        "input" is a list, so the batching the endpoint would normally do is
        performed here instead: one request per text, issued concurrently and
        recombined.

        `asyncio.gather` preserves argument order, which is load-bearing - the
        caller zips these vectors back onto chunks by position, so a reordered
        result would silently attach every embedding to the wrong chunk.
        """
        if not texts:
            return []

        if not self.is_live:
            return [self._mock_embedding(text) for text in texts]

        client = await self._get_client()
        # Bounded so that ingesting a large document does not open one
        # connection per chunk at once, which would exhaust the pool and draw
        # 429s from the provider. Concurrency still hides most of the latency.
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_EMBEDDING_REQUESTS)
        embeddings = await asyncio.gather(
            *(self._embed_one(client, text, semaphore) for text in texts)
        )

        for embedding in embeddings:
            if len(embedding) != EMBEDDING_DIMENSIONS:
                # The pgvector column width is fixed at DDL time, so realigning
                # means editing EMBEDDING_DIMENSIONS *and* recreating
                # document_chunks - create_all will not alter an existing
                # VECTOR(n) column.
                logger.error(
                    "Embedding width mismatch: %s returned %d dimensions, expected %d. "
                    "Set EMBEDDING_DIMENSIONS=%d and recreate the document_chunks table.",
                    self._settings.embedding_model,
                    len(embedding),
                    EMBEDDING_DIMENSIONS,
                    len(embedding),
                )
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"Embedding API returned {len(embedding)} dimensions, "
                        f"expected {EMBEDDING_DIMENSIONS}"
                    ),
                )

        return list(embeddings)

    async def _embed_one(
        self, client: httpx.AsyncClient, text: str, semaphore: asyncio.Semaphore
    ) -> list[float]:
        """Embeds a single string, retrying on 429. One text per request.

        The semaphore is held across retries on purpose: releasing it during a
        backoff would let a queued request start immediately and keep the
        pressure on an endpoint that just asked us to slow down.
        """
        async with semaphore:
            response = await self._post_with_retry(client, text)

        payload = response.json()
        try:
            return payload["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as exc:
            # A 200 with an unexpected shape would otherwise surface as a 500.
            logger.error("Embedding API returned an unexpected payload shape: %r", payload)
            raise HTTPException(
                status_code=502, detail="Embedding API returned a malformed response"
            ) from exc

    async def _post_with_retry(self, client: httpx.AsyncClient, text: str) -> httpx.Response:
        """POSTs one embedding request, backing off and retrying on 429.

        Only 429 is retried. A 400 or 401 is deterministic - retrying it just
        burns quota and delays a failure the caller needs to see.
        """
        for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
            await self._pacer.wait()
            try:
                response = await client.post(
                    "/embeddings",
                    # NOTE: two constraints of Gemini's OpenAI-compatibility
                    # layer are encoded here, both of which return 400 if
                    # violated: "input" must be a single string, never a list,
                    # and no "dimensions" field may be sent.
                    json={"model": self._settings.embedding_model, "input": text},
                    headers={"Authorization": f"Bearer {self._settings.together_api_key}"},
                )
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                is_rate_limited = exc.response.status_code == 429
                if not is_rate_limited or attempt == _MAX_RATE_LIMIT_RETRIES:
                    logger.error(
                        "Embedding API request failed (model=%s, status=%s, attempt=%d): %r",
                        self._settings.embedding_model,
                        exc.response.status_code,
                        attempt + 1,
                        exc,
                    )
                    raise HTTPException(
                        status_code=502, detail=f"Embedding API request failed: {exc}"
                    ) from exc

                delay = self._retry_delay(exc.response, attempt)
                logger.warning(
                    "Embedding API rate limited (429); retrying in %.1fs (attempt %d/%d)",
                    delay,
                    attempt + 1,
                    _MAX_RATE_LIMIT_RETRIES,
                )
                await asyncio.sleep(delay)
            except httpx.HTTPError as exc:
                # Transport-level failure (timeout, connection reset). No
                # status to inspect, so it is not retried here.
                logger.error(
                    "Embedding API request failed (model=%s): %r",
                    self._settings.embedding_model,
                    exc,
                )
                raise HTTPException(
                    status_code=502, detail=f"Embedding API request failed: {exc}"
                ) from exc

        # Unreachable: the final attempt either returns or raises above.
        raise HTTPException(status_code=502, detail="Embedding API request failed")

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        """Server-provided Retry-After when present, else jittered backoff.

        Retry-After is authoritative - it is the provider telling us exactly
        when the window reopens. Jitter matters on the fallback path because
        every chunk of a document is rate limited at nearly the same instant;
        without it they would all wake together and reproduce the burst that
        triggered the 429.
        """
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                # HTTP-date form; fall through to exponential backoff rather
                # than parsing a format this API is not documented to send.
                pass
        delay = self._base_retry_delay * (2**attempt)
        # Jitter is proportional to the delay, not a flat addend: it must
        # scale with the backoff and vanish when the delay is zero.
        return delay + random.uniform(0, delay / 2)

    @staticmethod
    def _mock_embedding(text: str) -> list[float]:
        """Deterministic pseudo-embedding derived from a SHA-256 hash of `text`,
        seeded into a NumPy RNG and L2-normalized to unit length."""
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        seed = struct.unpack(">I", digest[:4])[0]
        rng = np.random.default_rng(seed)
        vector = rng.standard_normal(EMBEDDING_DIMENSIONS)
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector = vector / norm
        return vector.tolist()
