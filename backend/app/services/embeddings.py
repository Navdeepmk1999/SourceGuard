import asyncio
import hashlib
import logging
import struct

import httpx
import numpy as np
from fastapi import HTTPException

from app.core.config import EMBEDDING_DIMENSIONS, Settings, get_settings

logger = logging.getLogger(__name__)

# Caps in-flight embedding requests. Gemini requires one text per request,
# so a 500-chunk document would otherwise fire 500 concurrent calls.
_MAX_CONCURRENT_EMBEDDING_REQUESTS = 8


class EmbeddingService:
    """Generates `EMBEDDING_DIMENSIONS`-dim embeddings via an external, OpenAI-
    compatible embeddings API (Together AI by default). Falls back to a
    deterministic mock embedding generator when no API key is configured, so
    the service works for local development and tests without network access."""

    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None

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
        """Embeds a single string. One text per request - see `embed_batch`."""
        async with semaphore:
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
            except httpx.HTTPError as exc:
                # Logged in full server-side; the client only needs to know the
                # upstream call failed.
                logger.error(
                    "Embedding API request failed (model=%s): %r",
                    self._settings.embedding_model,
                    exc,
                )
                raise HTTPException(
                    status_code=502, detail=f"Embedding API request failed: {exc}"
                ) from exc

        payload = response.json()
        try:
            return payload["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as exc:
            # A 200 with an unexpected shape would otherwise surface as a 500.
            logger.error("Embedding API returned an unexpected payload shape: %r", payload)
            raise HTTPException(
                status_code=502, detail="Embedding API returned a malformed response"
            ) from exc

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
