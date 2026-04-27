from __future__ import annotations

import asyncio
import logging

import httpx

from googledriverag.config import EmbeddingConfig

logger = logging.getLogger(__name__)


class EmbeddingClient:
    def __init__(self, config: EmbeddingConfig):
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self._semaphore = asyncio.Semaphore(5)
        self._api_call_store = None
        self._call_context: dict = {}

    def set_api_call_store(self, store):
        self._api_call_store = store

    def set_call_context(self, **kwargs):
        self._call_context = kwargs

    def clear_call_context(self):
        self._call_context = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client

    async def embed(self, text: str, call_context: dict | None = None) -> list[float]:
        results = await self.embed_batch([text], call_context=call_context)
        return results[0]

    async def embed_batch(self, texts: list[str], call_context: dict | None = None) -> list[list[float]]:
        if not texts:
            return []
        async with self._semaphore:
            client = await self._get_client()
            resp = await client.post(
                f"{self.config.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.config.api_key}"},
                json={
                    "model": self.config.model,
                    "input": texts,
                    "dimensions": self.config.dimensions,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            self._record_call(len(texts), data, call_context)
            sorted_data = sorted(data["data"], key=lambda x: x["index"])
            return [item["embedding"] for item in sorted_data]

    def _record_call(self, text_count: int, response_data: dict, call_context: dict | None = None):
        if not self._api_call_store:
            return
        try:
            usage = response_data.get("usage", {})
            ctx = call_context if call_context is not None else self._call_context
            self._api_call_store.record_call(
                call_type="embedding",
                model=self.config.model,
                operation=ctx.get("operation", ""),
                document_name=ctx.get("document_name", ""),
                chunk_id=ctx.get("chunk_id", ""),
                input_tokens=usage.get("prompt_tokens", usage.get("total_tokens", 0)),
                output_tokens=0,
                namespace=ctx.get("namespace", ""),
            )
        except Exception as e:
            logger.debug("Failed to record embedding API call: %s", e)

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
