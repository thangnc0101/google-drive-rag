from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass

from googledriverag.config import RetrievalConfig
from googledriverag.core.embedding_client import EmbeddingClient
from googledriverag.core.llm_client import LLMClient
from googledriverag.core.rrf import rrf_merge_multi_namespace
from googledriverag.services.namespace_manager import NamespaceManager
from googledriverag.services.retrieval_service import RetrievalService

logger = logging.getLogger(__name__)

KEYWORD_EXTRACTION_PROMPT = """Given the following query, extract keywords for search.

Query: {query}

Return JSON:
{{
  "high_level_keywords": ["concept1", "concept2"],
  "low_level_keywords": ["entity1", "entity2"]
}}

high_level_keywords: broad concepts, topics, themes
low_level_keywords: specific entities, names, technical terms

Return valid JSON only, no markdown fences."""

RAG_SYSTEM_PROMPT = """You are a helpful assistant that answers questions strictly based on the provided context.
You must ONLY use information from the provided context below. Do NOT use your own knowledge or make up any information.
If the context does not contain enough information to answer the question, clearly state that the provided documents do not contain the relevant information.
Always respond in the same language as the user's question.
Always cite your sources using [ref_number] format (e.g. [1], [2]) corresponding to the ref numbers in the Document Chunks.
{markdown_instruction}

{context}"""

TOKEN_ESTIMATE_RATIO = 4  # ~4 chars per token


@dataclass
class ExtractedKeywords:
    high_level: list[str]
    low_level: list[str]


class QueryService:
    def __init__(
        self,
        llm: LLMClient,
        embedding: EmbeddingClient,
        retrieval: RetrievalService,
        ns_manager: NamespaceManager,
        config: RetrievalConfig,
    ):
        self.llm = llm
        self.embedding = embedding
        self.retrieval = retrieval
        self.ns_manager = ns_manager
        self.config = config

    async def query(self, query_text: str, namespaces: list[str] | None = None,
                    mode: str | None = None, top_k: int | None = None) -> dict:
        t_total = time.perf_counter()
        mode = mode or self.config.default_mode
        top_k = top_k or self.config.top_k
        ns_list = namespaces if namespaces else self.ns_manager.list_all_names()

        if not ns_list:
            return {"answer": "No namespaces available.", "sources": [], "chunks": []}

        if mode == "bypass":
            ns_label = ",".join(ns_list)
            ctx = dict(operation="bypass_query", namespace=ns_label)
            answer = await self.llm.complete(query_text, model_type="query", call_context=ctx)
            return {"answer": answer, "sources": [], "chunks": [], "entities": [], "relations": []}

        t0 = time.perf_counter()
        ns_label = ",".join(ns_list)
        keywords = await self._extract_keywords(query_text, namespace=ns_label)
        logger.debug("[TIMING] keyword_extraction: %.3fs", time.perf_counter() - t0)

        texts_to_embed = [
            query_text,
            " ".join(keywords.low_level) if keywords.low_level else query_text,
            " ".join(keywords.high_level) if keywords.high_level else query_text,
        ]
        t0 = time.perf_counter()
        embeddings = await self.embedding.embed_batch(
            texts_to_embed,
            call_context=dict(operation="query_embedding", namespace=ns_label),
        )
        logger.debug("[TIMING] embedding: %.3fs", time.perf_counter() - t0)
        query_vec, ll_vec, hl_vec = embeddings[0], embeddings[1], embeddings[2]

        t0 = time.perf_counter()
        ns_results = []
        for ns in ns_list:
            try:
                result = self.retrieval.search(
                    ns_name=ns, mode=mode,
                    query_vec=query_vec, ll_vec=ll_vec, hl_vec=hl_vec,
                    query_text=query_text, top_k=top_k,
                )
                ns_results.append(result)
            except Exception as e:
                logger.warning("Search failed for namespace %s: %s", ns, e)
        logger.debug("[TIMING] retrieval_search (all namespaces): %.3fs", time.perf_counter() - t0)

        t0 = time.perf_counter()
        merged = rrf_merge_multi_namespace(ns_results, k=self.config.rrf_k)
        logger.debug("[TIMING] rrf_merge: %.3fs", time.perf_counter() - t0)

        t0 = time.perf_counter()
        context = self._build_context(merged)
        markdown_instruction = (
            ""
            if self.config.query_response_markdown
            else "Do NOT use markdown formatting in your response. Respond in plain text only."
        )
        system_prompt = RAG_SYSTEM_PROMPT.format(
            context=context,
            markdown_instruction=markdown_instruction,
        )
        answer = await self.llm.complete_with_system(
            system_prompt, query_text, model_type="query",
            call_context=dict(operation="answer_generation", namespace=ns_label),
        )
        logger.debug("[TIMING] answer_generation: %.3fs", time.perf_counter() - t0)

        logger.debug("[TIMING] query_total: %.3fs", time.perf_counter() - t_total)

        cited_refs = self._extract_cited_refs(answer)
        sources = self._build_sources(merged, cited_refs)

        if not self.config.show_references:
            answer = re.sub(r"\s*\[\d+\]", "", answer)

        return {
            "answer": answer,
            "sources": sources,
            "chunks": merged["chunks"][:top_k],
            "entities": merged.get("entities", [])[:20],
            "relations": merged.get("relations", [])[:20],
        }

    async def retrieve_only(self, query_text: str, namespaces: list[str] | None = None,
                            mode: str | None = None, top_k: int | None = None) -> dict:
        mode = mode or self.config.default_mode
        top_k = top_k or self.config.top_k
        ns_list = namespaces if namespaces else self.ns_manager.list_all_names()

        if not ns_list:
            return {"chunks": []}

        ns_label = ",".join(ns_list)
        keywords = await self._extract_keywords(query_text, namespace=ns_label)
        texts_to_embed = [
            query_text,
            " ".join(keywords.low_level) if keywords.low_level else query_text,
            " ".join(keywords.high_level) if keywords.high_level else query_text,
        ]
        embeddings = await self.embedding.embed_batch(
            texts_to_embed,
            call_context=dict(operation="retrieve_embedding", namespace=ns_label),
        )
        query_vec, ll_vec, hl_vec = embeddings[0], embeddings[1], embeddings[2]

        ns_results = []
        for ns in ns_list:
            try:
                result = self.retrieval.search(
                    ns_name=ns, mode=mode,
                    query_vec=query_vec, ll_vec=ll_vec, hl_vec=hl_vec,
                    query_text=query_text, top_k=top_k,
                )
                ns_results.append(result)
            except Exception as e:
                logger.warning("Search failed for namespace %s: %s", ns, e)

        merged = rrf_merge_multi_namespace(ns_results, k=self.config.rrf_k)
        return {"chunks": merged["chunks"][:top_k]}

    async def _extract_keywords(self, query: str, namespace: str = "") -> ExtractedKeywords:
        try:
            prompt = KEYWORD_EXTRACTION_PROMPT.format(query=query)
            ctx = dict(operation="keyword_extraction", namespace=namespace)
            response = await self.llm.complete(prompt, model_type="enrichment", call_context=ctx)
            text = response.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:])
                if text.endswith("```"):
                    text = text[:-3]
            data = json.loads(text)
            result = ExtractedKeywords(
                high_level=data.get("high_level_keywords", []),
                low_level=data.get("low_level_keywords", []),
            )
        except Exception:
            result = ExtractedKeywords(high_level=[], low_level=[])

        if not result.high_level and not result.low_level:
            result.low_level = [query]
        return result

    def _build_context(self, merged: dict) -> str:
        parts = []
        max_entity_tokens = self.config.max_entity_tokens
        max_relation_tokens = self.config.max_relation_tokens
        max_total_tokens = self.config.max_total_tokens

        entities = merged.get("entities", [])
        if entities:
            entity_json = []
            used_tokens = 0
            for e in entities:
                entry = {"entity": e.get("name", ""), "type": e.get("entity_type", ""), "description": e.get("description", "")}
                entry_tokens = len(json.dumps(entry, ensure_ascii=False)) // TOKEN_ESTIMATE_RATIO
                if used_tokens + entry_tokens > max_entity_tokens:
                    break
                entity_json.append(entry)
                used_tokens += entry_tokens
            if entity_json:
                parts.append(f"Knowledge Graph Entities:\n{json.dumps(entity_json, ensure_ascii=False)}")

        relations = merged.get("relations", [])
        if relations:
            rel_json = []
            used_tokens = 0
            for r in relations:
                entry = {"source": r.get("source", ""), "target": r.get("target", ""), "description": r.get("description", "")}
                entry_tokens = len(json.dumps(entry, ensure_ascii=False)) // TOKEN_ESTIMATE_RATIO
                if used_tokens + entry_tokens > max_relation_tokens:
                    break
                rel_json.append(entry)
                used_tokens += entry_tokens
            if rel_json:
                parts.append(f"Knowledge Graph Relations:\n{json.dumps(rel_json, ensure_ascii=False)}")

        kg_context = "\n\n".join(parts) if parts else ""
        kg_tokens = len(kg_context) // TOKEN_ESTIMATE_RATIO
        sys_prompt_base_tokens = len(RAG_SYSTEM_PROMPT) // TOKEN_ESTIMATE_RATIO
        buffer_tokens = 200
        available_chunk_tokens = max(
            500,
            max_total_tokens - kg_tokens - sys_prompt_base_tokens - buffer_tokens,
        )

        chunks = merged.get("chunks", [])
        if chunks:
            chunk_json = []
            used_tokens = 0
            for i, c in enumerate(chunks):
                entry = {"ref": i + 1, "source": c.get("source", ""), "namespace": c.get("namespace", ""), "text": c.get("text", "")}
                entry_tokens = len(json.dumps(entry, ensure_ascii=False)) // TOKEN_ESTIMATE_RATIO
                if used_tokens + entry_tokens > available_chunk_tokens:
                    break
                chunk_json.append(entry)
                used_tokens += entry_tokens
            if chunk_json:
                parts.append(f"Document Chunks:\n{json.dumps(chunk_json, ensure_ascii=False)}")

        return "\n\n".join(parts) if parts else "No relevant context found."

    def _extract_cited_refs(self, answer: str) -> set[int]:
        return set(int(m) for m in re.findall(r"\[(\d+)\]", answer))

    def _build_sources(self, merged: dict, cited_refs: set[int]) -> list[dict]:
        sources = []
        seen = set()
        chunks = merged.get("chunks", [])[:10]
        for i, chunk in enumerate(chunks):
            ref_num = i + 1
            if ref_num not in cited_refs:
                continue
            source_key = (chunk.get("source", ""), chunk.get("namespace", ""))
            if source_key not in seen:
                seen.add(source_key)
                doc_id = chunk.get("source", "")
                ns_name = chunk.get("namespace", "")
                file_name = ""
                url = ""
                if ns_name and doc_id:
                    try:
                        storage = self.ns_manager.get_storage(ns_name)
                        doc_record = storage.sqlite.get_document(doc_id)
                        if doc_record:
                            file_name = doc_record.name
                            url = doc_record.url
                    except Exception:
                        pass
                sources.append({
                    "document": doc_id,
                    "namespace": ns_name,
                    "chunk_id": chunk.get("chunk_id", ""),
                    "page": chunk.get("page"),
                    "file_name": file_name,
                    "url": url,
                })
        return sources
