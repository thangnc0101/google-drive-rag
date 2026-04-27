from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

from googledriverag.core.chunker import Chunk
from googledriverag.core.llm_client import LLMClient

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """Given the following text chunk from document "{filename}":

---
{text}
---

Extract the following in JSON format:
{{
  "entities": [
    {{"name": "...", "type": "person|org|product|concept|location|event|other", "description": "..."}}
  ],
  "relationships": [
    {{"source": "entity_name", "target": "entity_name", "keywords": "...", "description": "..."}}
  ],
  "keywords": ["keyword1", "keyword2"],
  "summary": "1-2 sentence summary"
}}

Rules:
- Entity names should be normalized (capitalize proper nouns, lowercase concepts)
- Skip self-referencing relationships (source == target)
- Return valid JSON only, no markdown fences"""


BATCH_CONTEXTUAL_PROMPT = """Below are {count} consecutive text chunks from document "{filename}".
For each chunk, list contextual keywords that are NOT in the chunk text but help understand it (e.g. parent topic, related terms from neighbor chunks).

{chunks_block}

Reply with exactly {count} lines. Each line = comma-separated keywords for that chunk.
Separate each line with "---" on its own line.
Example for 3 chunks:
keyword1, keyword2, keyword3
---
keyword4, keyword5
---
keyword6, keyword7, keyword8"""


GLEANING_PROMPT = """The previous extraction may have missed some entities or relationships.
Please review the original text again and extract any ADDITIONAL entities and relationships that were not captured before.

Original text from document "{filename}":
---
{text}
---

Previous extraction found: {prev_entity_count} entities, {prev_rel_count} relationships.

Return ONLY the additional/missed items in the same JSON format:
{{
  "entities": [
    {{"name": "...", "type": "person|org|product|concept|location|event|other", "description": "..."}}
  ],
  "relationships": [
    {{"source": "entity_name", "target": "entity_name", "keywords": "...", "description": "..."}}
  ]
}}

Return valid JSON only, no markdown fences. If nothing was missed, return empty arrays."""


@dataclass
class EntityExtraction:
    name: str
    type: str
    description: str


@dataclass
class RelationExtraction:
    source: str
    target: str
    keywords: str
    description: str


@dataclass
class EnrichmentResult:
    entities: list[EntityExtraction] = field(default_factory=list)
    relationships: list[RelationExtraction] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    summary: str = ""


class LLMEnrichment:
    def __init__(self, llm_client: LLMClient, max_concurrent: int = 5,
                 enable_gleaning: bool = False,
                 enable_batch_contextual: bool = False,
                 batch_contextual_size: int = 5):
        self.llm = llm_client
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.enable_gleaning = enable_gleaning
        self.enable_batch_contextual = enable_batch_contextual
        self.batch_contextual_size = batch_contextual_size

    async def enrich_chunk(self, chunk: Chunk, filename: str, namespace: str = "") -> EnrichmentResult:
        prompt = EXTRACTION_PROMPT.format(filename=filename, text=chunk.text)
        async with self.semaphore:
            try:
                ctx = dict(operation="entity_extraction", document_name=filename, chunk_id=chunk.chunk_id, namespace=namespace)
                response = await self.llm.complete(prompt, model_type="enrichment", call_context=ctx)
                result = self._parse_response(response)

                if self.enable_gleaning:
                    gleaned = await self._glean(chunk, filename, result, namespace=namespace)
                    result = self._merge_results(result, gleaned)

                return result
            except Exception as e:
                logger.warning("Enrichment failed for chunk %s: %s", chunk.chunk_id, e)
                return EnrichmentResult()

    async def _glean(self, chunk: Chunk, filename: str, prev: EnrichmentResult, namespace: str = "") -> EnrichmentResult:
        prompt = GLEANING_PROMPT.format(
            filename=filename, text=chunk.text,
            prev_entity_count=len(prev.entities),
            prev_rel_count=len(prev.relationships),
        )
        try:
            ctx = dict(operation="gleaning", document_name=filename, chunk_id=chunk.chunk_id, namespace=namespace)
            response = await self.llm.complete(prompt, model_type="enrichment", call_context=ctx)
            return self._parse_response(response)
        except Exception as e:
            logger.warning("Gleaning failed for chunk %s: %s", chunk.chunk_id, e)
            return EnrichmentResult()

    def _merge_results(self, base: EnrichmentResult, extra: EnrichmentResult) -> EnrichmentResult:
        existing_entities = {e.name.lower() for e in base.entities}
        for e in extra.entities:
            if e.name.lower() not in existing_entities:
                base.entities.append(e)
                existing_entities.add(e.name.lower())

        existing_rels = {(r.source.lower(), r.target.lower()) for r in base.relationships}
        for r in extra.relationships:
            key = (r.source.lower(), r.target.lower())
            rev_key = (r.target.lower(), r.source.lower())
            if key not in existing_rels and rev_key not in existing_rels:
                base.relationships.append(r)
                existing_rels.add(key)

        return base

    async def enrich_chunks(
        self, chunks: list[Chunk], filename: str, namespace: str = "",
    ) -> tuple[list[EnrichmentResult], list[list[str]]]:
        if self.enable_batch_contextual:
            contextual_keywords = await self._batch_contextual_keywords(chunks, filename, namespace)
        else:
            contextual_keywords = [[] for _ in chunks]

        tasks = [
            self._enrich_chunk_with_context(c, filename, namespace,
                                            contextual_keywords[i])
            for i, c in enumerate(chunks)
        ]
        results = await asyncio.gather(*tasks)
        return results, contextual_keywords

    async def _enrich_chunk_with_context(
        self, chunk: Chunk, filename: str, namespace: str,
        extra_keywords: list[str],
    ) -> EnrichmentResult:
        if extra_keywords:
            enriched_text = chunk.text + "\n\nContextual keywords: " + ", ".join(extra_keywords)
        else:
            enriched_text = chunk.text
        result = await self._enrich_with_text(chunk, enriched_text, filename, namespace)
        if extra_keywords:
            existing = {k.lower().strip() for k in result.keywords}
            for kw in extra_keywords:
                if kw.lower().strip() not in existing:
                    result.keywords.append(kw)
                    existing.add(kw.lower().strip())
        return result

    async def _enrich_with_text(
        self, chunk: Chunk, text: str, filename: str, namespace: str,
    ) -> EnrichmentResult:
        prompt = EXTRACTION_PROMPT.format(filename=filename, text=text)
        async with self.semaphore:
            try:
                ctx = dict(operation="entity_extraction", document_name=filename,
                           chunk_id=chunk.chunk_id, namespace=namespace)
                response = await self.llm.complete(prompt, model_type="enrichment", call_context=ctx)
                result = self._parse_response(response)

                if self.enable_gleaning:
                    gleaned = await self._glean(chunk, filename, result, namespace=namespace)
                    result = self._merge_results(result, gleaned)

                return result
            except Exception as e:
                logger.warning("Enrichment failed for chunk %s: %s", chunk.chunk_id, e)
                return EnrichmentResult()

    async def _batch_contextual_keywords(
        self, chunks: list[Chunk], filename: str, namespace: str,
    ) -> list[list[str]]:
        batch_size = self.batch_contextual_size
        all_keywords: list[list[str]] = [[] for _ in chunks]
        batches = []
        for start in range(0, len(chunks), batch_size):
            end = min(start + batch_size, len(chunks))
            batches.append((start, end))

        tasks = [
            self._process_contextual_batch(chunks[s:e], filename, namespace)
            for s, e in batches
        ]
        batch_results = await asyncio.gather(*tasks)

        for (start, _), keywords_list in zip(batches, batch_results):
            for i, kws in enumerate(keywords_list):
                if start + i < len(all_keywords):
                    all_keywords[start + i] = kws

        return all_keywords

    async def _process_contextual_batch(
        self, batch_chunks: list[Chunk], filename: str, namespace: str,
    ) -> list[list[str]]:
        count = len(batch_chunks)
        chunks_block = "\n\n".join(
            f"[Chunk {i + 1}]:\n{c.text}" for i, c in enumerate(batch_chunks)
        )
        prompt = BATCH_CONTEXTUAL_PROMPT.format(
            count=count, filename=filename, chunks_block=chunks_block,
        )
        async with self.semaphore:
            try:
                ctx = dict(operation="batch_contextual_enrichment",
                           document_name=filename, namespace=namespace)
                response = await self.llm.complete(prompt, model_type="enrichment", call_context=ctx)
                return self._parse_batch_contextual_response(response, count)
            except Exception as e:
                logger.warning("Batch contextual enrichment failed: %s", e)
                return [[] for _ in range(count)]

    @staticmethod
    def _parse_batch_contextual_response(response: str, expected_count: int) -> list[list[str]]:
        text = response.strip()
        parts = [p.strip() for p in text.split("---") if p.strip()]
        result: list[list[str]] = []
        for part in parts[:expected_count]:
            keywords = [kw.strip() for kw in part.split(",") if kw.strip()]
            result.append(keywords)
        while len(result) < expected_count:
            result.append([])
        return result

    def _parse_response(self, response: str) -> EnrichmentResult:
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:])
            if text.endswith("```"):
                text = text[:-3]

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM enrichment JSON")
            return EnrichmentResult()

        entities = []
        for e in data.get("entities", []):
            name = e.get("name", "").strip()
            if not name:
                continue
            entities.append(EntityExtraction(
                name=name,
                type=e.get("type", "other").lower().strip(),
                description=e.get("description", ""),
            ))

        relationships = []
        for r in data.get("relationships", []):
            src = r.get("source", "").strip()
            tgt = r.get("target", "").strip()
            if not src or not tgt or src.lower() == tgt.lower():
                continue
            relationships.append(RelationExtraction(
                source=src,
                target=tgt,
                keywords=r.get("keywords", ""),
                description=r.get("description", ""),
            ))

        return EnrichmentResult(
            entities=entities,
            relationships=relationships,
            keywords=data.get("keywords", []),
            summary=data.get("summary", ""),
        )
