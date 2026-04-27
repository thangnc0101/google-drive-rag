from __future__ import annotations

import asyncio
import json
import logging

import httpx

from googledriverag.config import LLMConfig

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self, config: LLMConfig):
        self.config = config
        self.semaphore = asyncio.Semaphore(config.max_concurrent_requests)
        self._client: httpx.AsyncClient | None = None
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
            self._client = httpx.AsyncClient(timeout=120.0)
        return self._client

    async def complete(
        self,
        prompt: str,
        model_type: str = "enrichment",
        system: str | None = None,
        call_context: dict | None = None,
    ) -> str:
        model = (
            self.config.enrichment_model
            if model_type == "enrichment"
            else self.config.query_model
        )
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        async with self.semaphore:
            client = await self._get_client()
            resp = await client.post(
                f"{self.config.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.config.api_key}"},
                json={"model": model, "messages": messages, "temperature": 0},
            )
            resp.raise_for_status()
            data = resp.json()
            self._record_call(model, data, call_context)
            return data["choices"][0]["message"]["content"]

    async def complete_with_system(
        self,
        system: str,
        user: str,
        model_type: str = "query",
        call_context: dict | None = None,
    ) -> str:
        return await self.complete(user, model_type=model_type, system=system, call_context=call_context)

    def _record_call(self, model: str, response_data: dict, call_context: dict | None = None):
        if not self._api_call_store:
            return
        try:
            usage = response_data.get("usage", {})
            ctx = call_context if call_context is not None else self._call_context
            self._api_call_store.record_call(
                call_type="llm",
                model=model,
                operation=ctx.get("operation", ""),
                document_name=ctx.get("document_name", ""),
                chunk_id=ctx.get("chunk_id", ""),
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                namespace=ctx.get("namespace", ""),
            )
        except Exception as e:
            logger.debug("Failed to record LLM API call: %s", e)

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
