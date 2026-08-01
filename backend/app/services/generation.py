import json
from collections.abc import AsyncIterator

import httpx
from fastapi import HTTPException

from app.core.config import Settings, get_settings


class GenerationService:
    """Streams answer tokens from Groq's OpenAI-compatible chat completions API.

    Falls back to a deterministic mock token stream (built from the retrieved
    context, no network) when no GROQ_API_KEY is configured, so the query
    pipeline is usable for local development and tests without live API access.
    """

    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None

    @property
    def is_live(self) -> bool:
        return bool(self._settings.groq_api_key)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._settings.groq_api_base, timeout=30.0)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def stream_answer(self, query: str, context_chunks: list[str]) -> AsyncIterator[str]:
        """Yields answer tokens one at a time."""
        if not self.is_live:
            async for token in self._mock_stream(query, context_chunks):
                yield token
            return

        client = await self._get_client()
        prompt = self._build_prompt(query, context_chunks)
        try:
            async with client.stream(
                "POST",
                "/chat/completions",
                json={
                    "model": self._settings.groq_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": True,
                },
                headers={"Authorization": f"Bearer {self._settings.groq_api_key}"},
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        break
                    payload = json.loads(data)
                    delta = payload["choices"][0]["delta"].get("content")
                    if delta:
                        yield delta
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Generation API request failed: {exc}") from exc

    @staticmethod
    def _build_prompt(query: str, context_chunks: list[str]) -> str:
        context = "\n\n".join(context_chunks)
        return (
            f"Context:\n{context}\n\nQuestion: {query}\n"
            "Answer using only the information in the context above."
        )

    @staticmethod
    async def _mock_stream(query: str, context_chunks: list[str]) -> AsyncIterator[str]:
        if not context_chunks:
            answer = "No relevant context was found to answer this query."
        else:
            answer = f"Based on the retrieved context, {context_chunks[0]}"
        for word in answer.split(" "):
            yield word + " "
