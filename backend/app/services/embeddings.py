import hashlib
import logging
import struct

import httpx
import numpy as np
from fastapi import HTTPException

from app.core.config import EMBEDDING_DIMENSIONS, Settings, get_settings

logger = logging.getLogger(__name__)


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
        if not texts:
            return []

        if not self.is_live:
            return [self._mock_embedding(text) for text in texts]

        client = await self._get_client()
        try:
            response = await client.post(
                "/embeddings",
                # NOTE: do not add a "dimensions" field here. Gemini's
                # OpenAI-compatibility layer returns 400 Bad Request when it is
                # present, so the vector width is whatever the model natively
                # emits and EMBEDDING_DIMENSIONS must be set to match it.
                json={"model": self._settings.embedding_model, "input": texts},
                headers={"Authorization": f"Bearer {self._settings.together_api_key}"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # Logged in full server-side because a 400 here most likely means
            # the provider's OpenAI-compatibility layer rejected `dimensions`;
            # the client only needs to know the upstream call failed.
            logger.error(
                "Embedding API request failed (model=%s, dimensions=%s): %r",
                self._settings.embedding_model,
                EMBEDDING_DIMENSIONS,
                exc,
            )
            raise HTTPException(status_code=502, detail=f"Embedding API request failed: {exc}") from exc

        payload = response.json()
        embeddings = [item["embedding"] for item in payload.get("data", [])]

        for embedding in embeddings:
            if len(embedding) != EMBEDDING_DIMENSIONS:
                # The provider ignored the requested width. Name the remedy in
                # the log: the pgvector column width is set at DDL time, so
                # realigning means editing EMBEDDING_DIMENSIONS *and*
                # recreating document_chunks - create_all will not alter it.
                logger.error(
                    "Embedding width mismatch: %s returned %d dimensions, expected %d. "
                    "The provider ignored the requested `dimensions`. Either use an "
                    "endpoint that honors it, or set EMBEDDING_DIMENSIONS=%d and "
                    "recreate the document_chunks table.",
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

        return embeddings

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
