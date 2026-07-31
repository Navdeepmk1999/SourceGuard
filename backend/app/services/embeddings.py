import hashlib
import struct

import httpx
import numpy as np
from fastapi import HTTPException

from app.core.config import EMBEDDING_DIMENSIONS, Settings, get_settings


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
                json={"model": self._settings.embedding_model, "input": texts},
                headers={"Authorization": f"Bearer {self._settings.together_api_key}"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Embedding API request failed: {exc}") from exc

        payload = response.json()
        embeddings = [item["embedding"] for item in payload.get("data", [])]

        for embedding in embeddings:
            if len(embedding) != EMBEDDING_DIMENSIONS:
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
