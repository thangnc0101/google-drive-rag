from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from googledriverag.config import RetrievalConfig
from googledriverag.core.chunker import Chunk, Chunker
from googledriverag.core.document_parser import DocumentParser
from googledriverag.core.embedding_client import EmbeddingClient
from googledriverag.core.errors import ExternalAPIError
from googledriverag.core.llm_client import LLMClient
from googledriverag.core.llm_enrichment import (
    EnrichmentResult,
    LLMEnrichment,
)
from googledriverag.services.progress_store import ProgressStore
from googledriverag.storage.namespace_storage import NamespaceStorage
from googledriverag.storage.sqlite_store import ChunkRecord, DocumentRecord

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    doc_id: str
    filename: str
    chunks_count: int
    entities_count: int
    relationships_count: int


SUMMARY_PROMPT = """Summarize the following descriptions of a knowledge graph entity/relationship into a single concise description.
Preserve all key facts and details. Be concise but comprehensive.

Descriptions:
{descriptions}

Return only the summarized description, no extra formatting."""


class IngestionService:
    def __init__(
        self,
        parser: DocumentParser,
        chunker: Chunker,
        enrichment: LLMEnrichment,
        embedding_client: EmbeddingClient,
        llm_client: LLMClient | None = None,
        retrieval_config: RetrievalConfig | None = None,
        progress_store: ProgressStore | None = None,
    ):
        self.parser = parser
        self.chunker = chunker
        self.enrichment = enrichment
        self.embedding = embedding_client
        self.llm = llm_client
        self.config = retrieval_config or RetrievalConfig()
        self.progress = progress_store

    async def ingest_document(
        self,
        file_bytes: bytes,
        filename: str,
        mime_type: str,
        drive_file_id: str,
        storage: NamespaceStorage,
        drive_modified_time: str = "",
        url: str = "",
        force_reindex: bool = False,
    ) -> IngestResult:
        storage.ensure_loaded()

        doc_id = f"gdrive-{drive_file_id}"
        content_hash = hashlib.md5(file_bytes).hexdigest()

        existing = storage.sqlite.get_document(doc_id)
        if not existing:
            legacy = storage.sqlite.get_document_by_drive_id(drive_file_id)
            if legacy and legacy.doc_id != doc_id:
                logger.info("Migrating document %s: old_id=%s -> new_id=%s", filename, legacy.doc_id, doc_id)
                self._purge_document_data(legacy.doc_id, storage)
                storage.sqlite.delete_document_row(legacy.doc_id)

        if existing and existing.status == "processing":
            logger.info("Document %s is already being processed, skipping", filename)
            return IngestResult(doc_id=doc_id, filename=filename, chunks_count=0,
                                entities_count=0, relationships_count=0)

        if existing and existing.content_hash == content_hash and not force_reindex:
            logger.debug("Document %s unchanged (hash=%s), skipping ingestion", filename, content_hash[:8])
            self._update_metadata_if_changed(existing, filename, drive_modified_time, url, len(file_bytes), storage)
            return IngestResult(doc_id=doc_id, filename=filename, chunks_count=0,
                                entities_count=0, relationships_count=0)

        is_reindex = existing is not None
        old_chunk_ids: list[str] = []
        if is_reindex:
            logger.info("Document %s content changed (old_hash=%s, new_hash=%s), re-indexing",
                        filename, existing.content_hash[:8] if existing.content_hash else "none", content_hash[:8])
            old_chunk_ids = [r.chunk_id for r in storage.sqlite.get_chunks_by_doc(doc_id)]

        await self._preflight_check(storage.ns_name)

        progress_key = f"{storage.ns_name}:{doc_id}"
        if self.progress:
            self.progress.start_document(progress_key, filename, storage.ns_name)

        now = datetime.now(timezone.utc).isoformat()
        storage.sqlite.upsert_document(DocumentRecord(
            doc_id=doc_id, name=filename, drive_file_id=drive_file_id,
            drive_modified_time=drive_modified_time or now, content_hash=content_hash,
            status="processing", synced_at=now, url=url, file_size=len(file_bytes),
        ))

        try:
            result = await self._do_ingest(
                file_bytes, filename, mime_type, drive_file_id, storage,
                doc_id, content_hash, drive_modified_time, url, progress_key,
            )
            if is_reindex and old_chunk_ids:
                self._purge_old_chunks(doc_id, old_chunk_ids, storage)
            storage.sqlite.update_document_status(doc_id, "indexed")
            return result
        except Exception as e:
            logger.error("Ingestion failed for %s (doc_id=%s): %s", filename, doc_id, e, exc_info=True)
            if is_reindex:
                logger.warning("Restoring previous record for %s after failed re-index", filename)
                storage.sqlite.upsert_document(existing)
            else:
                storage.sqlite.update_document_status(doc_id, "error")
            raise
        finally:
            if self.progress:
                self.progress.finish_document(progress_key)

    async def _preflight_check(self, namespace: str) -> None:
        """Verify external embedding API is reachable before mutating storage.

        Raises ExternalAPIError if the API is unreachable, quota-exceeded, or
        returns an auth/server error. Caller should abort ingestion without
        touching document state.
        """
        try:
            await self.embedding.embed_batch(
                ["ping"],
                call_context=dict(operation="preflight", namespace=namespace),
            )
        except ExternalAPIError:
            raise
        except Exception as e:
            raise ExternalAPIError(f"Preflight embedding check failed: {e}") from e

    def _update_metadata_if_changed(
        self, existing: DocumentRecord, filename: str,
        drive_modified_time: str, url: str, file_size: int,
        storage: NamespaceStorage,
    ) -> None:
        changed = False
        if drive_modified_time and existing.drive_modified_time != drive_modified_time:
            existing.drive_modified_time = drive_modified_time
            changed = True
        if filename and existing.name != filename:
            logger.debug("Updating name for %s: %s -> %s", existing.doc_id, existing.name, filename)
            existing.name = filename
            changed = True
        if url and existing.url != url:
            existing.url = url
            changed = True
        if file_size and existing.file_size != file_size:
            existing.file_size = file_size
            changed = True
        if changed:
            existing.synced_at = datetime.now(timezone.utc).isoformat()
            storage.sqlite.upsert_document(existing)

    def _purge_document_data(self, doc_id: str, storage: NamespaceStorage) -> None:
        old_chunk_ids = [
            r.chunk_id for r in storage.sqlite.get_chunks_by_doc(doc_id)
        ]

        orphaned_entities, orphaned_relations = (
            storage.sqlite.find_orphaned_after_chunk_delete(old_chunk_ids)
        )

        storage.sqlite.delete_chunks_by_doc(doc_id)
        for cid in old_chunk_ids:
            storage.vectors.chunks.delete(cid)

        for entity_name in orphaned_entities:
            storage.graph.remove_node(entity_name)
            storage.vectors.entities.delete(entity_name)
        for src, tgt in orphaned_relations:
            storage.graph.remove_edge(src, tgt)
            storage.vectors.relationships.delete(f"{src}||{tgt}")

    def _purge_old_chunks(self, doc_id: str, old_chunk_ids: list[str], storage: NamespaceStorage) -> None:
        current_chunk_ids = {r.chunk_id for r in storage.sqlite.get_chunks_by_doc(doc_id)}
        stale_ids = [cid for cid in old_chunk_ids if cid not in current_chunk_ids]
        if not stale_ids:
            return

        logger.debug("Purging %d stale chunks for %s", len(stale_ids), doc_id)
        orphaned_entities, orphaned_relations = (
            storage.sqlite.find_orphaned_after_chunk_delete(stale_ids)
        )

        storage.sqlite.delete_mappings_by_chunks(stale_ids)
        for cid in stale_ids:
            storage.sqlite.conn.execute("DELETE FROM chunks WHERE chunk_id = ?", (cid,))
            storage.vectors.chunks.delete(cid)
        storage.sqlite.conn.commit()

        for entity_name in orphaned_entities:
            storage.graph.remove_node(entity_name)
            storage.vectors.entities.delete(entity_name)
        for src, tgt in orphaned_relations:
            storage.graph.remove_edge(src, tgt)
            storage.vectors.relationships.delete(f"{src}||{tgt}")

    async def _do_ingest(
        self, file_bytes, filename, mime_type, drive_file_id, storage,
        doc_id, content_hash, drive_modified_time, url, progress_key,
    ) -> IngestResult:
        def _progress(stage: str, current: int = 0, total: int = 0):
            if self.progress:
                self.progress.update_document(progress_key, stage, current, total)

        _progress("parsing")
        doc = self.parser.parse(file_bytes, filename, mime_type)

        _progress("chunking")
        chunks = self.chunker.chunk(doc, doc_id=doc_id)
        if not chunks:
            return IngestResult(doc_id=doc_id, filename=filename, chunks_count=0,
                                entities_count=0, relationships_count=0)

        total_chunks = len(chunks)
        _progress("enriching", 0, total_chunks)
        enrichments, contextual_keywords = await self.enrichment.enrich_chunks(chunks, filename, namespace=storage.ns_name)
        _progress("enriching", total_chunks, total_chunks)
        logger.debug("Indexing %s: %d chunks, starting embedding", filename, total_chunks)

        _progress("embedding", 0, total_chunks)
        chunk_texts = []
        for i, c in enumerate(chunks):
            kws = contextual_keywords[i] if contextual_keywords else []
            if kws:
                chunk_texts.append(c.text + "\n\nContextual keywords: " + ", ".join(kws))
            else:
                chunk_texts.append(c.text)
        chunk_vectors = await self.embedding.embed_batch(
            chunk_texts,
            call_context=dict(operation="chunk_embedding", document_name=filename, namespace=storage.ns_name),
        )
        _progress("embedding", total_chunks, total_chunks)

        _progress("saving")
        chunk_records = []
        for chunk, enrichment in zip(chunks, enrichments):
            chunk_records.append(ChunkRecord(
                chunk_id=chunk.chunk_id,
                doc_id=doc_id,
                sequence_index=chunk.sequence_index,
                text=chunk.text,
                page=chunk.page,
                section=chunk.section,
                token_count=chunk.token_count,
                keywords=json.dumps(enrichment.keywords, ensure_ascii=False),
                expanded_keywords="[]",
                summary=enrichment.summary,
                category="",
            ))
        storage.sqlite.insert_chunks(chunk_records)

        for chunk, vector in zip(chunks, chunk_vectors):
            storage.vectors.chunks.add(chunk.chunk_id, vector, {"doc_id": doc_id})

        _progress("merging_entities")
        entity_count = await self._merge_entities(enrichments, chunks, storage, storage.ns_name)
        logger.debug("Indexing %s: %d entities merged, starting relationship embedding", filename, entity_count)

        _progress("merging_relationships")
        rel_count = await self._merge_relationships(enrichments, chunks, storage, storage.ns_name)
        logger.debug("Indexing %s: %d relationships merged", filename, rel_count)

        _progress("done")
        storage.graph.detect_communities()
        storage.vectors.save()
        storage.graph.save()

        logger.info("Ingested %s: %d chunks, %d entities, %d relations",
                     filename, len(chunks), entity_count, rel_count)

        return IngestResult(
            doc_id=doc_id, filename=filename, chunks_count=len(chunks),
            entities_count=entity_count, relationships_count=rel_count,
        )

    async def _merge_entities(
        self,
        enrichments: list[EnrichmentResult],
        chunks: list[Chunk],
        storage: NamespaceStorage,
        namespace: str = "",
    ) -> int:
        entity_map: dict[str, list[tuple]] = {}
        for enrichment, chunk in zip(enrichments, chunks):
            for entity in enrichment.entities:
                key = entity.name.lower().strip()
                if not key:
                    continue
                entity_map.setdefault(key, []).append((entity, chunk.chunk_id))

        prepared: list[tuple[str, str, str, list[str]]] = []
        for name, occurrences in entity_map.items():
            existing = storage.graph.get_node(name)
            type_ = Counter(e.type for e, _ in occurrences).most_common(1)[0][0]
            descriptions = [e.description for e, _ in occurrences if e.description]
            if existing and existing.get("description"):
                descriptions.insert(0, existing["description"])
            merged_desc = await self._merge_descriptions_async(descriptions, namespace=namespace)
            chunk_ids = [cid for _, cid in occurrences]
            prepared.append((name, type_, merged_desc, chunk_ids))

        embed_texts = [
            f"{name}\n{desc}" if desc else name
            for name, _, desc, _ in prepared
        ]
        if embed_texts:
            vectors = await self.embedding.embed_batch(
                embed_texts,
                call_context=dict(operation="entity_embedding", namespace=namespace),
            )
        else:
            vectors = []

        for (name, type_, merged_desc, chunk_ids), vector in zip(prepared, vectors):
            storage.graph.upsert_node(name, entity_type=type_, description=merged_desc)
            storage.vectors.entities.add(name, vector, {"name": name, "type": type_})
            storage.sqlite.upsert_entity_chunks(name, chunk_ids)

        return len(entity_map)

    async def _merge_relationships(
        self,
        enrichments: list[EnrichmentResult],
        chunks: list[Chunk],
        storage: NamespaceStorage,
        namespace: str = "",
    ) -> int:
        rel_map: dict[tuple[str, str], list[tuple]] = {}
        for enrichment, chunk in zip(enrichments, chunks):
            for rel in enrichment.relationships:
                src = rel.source.lower().strip()
                tgt = rel.target.lower().strip()
                if not src or not tgt:
                    continue
                key = tuple(sorted([src, tgt]))
                rel_map.setdefault(key, []).append((rel, chunk.chunk_id))

        prepared: list[tuple[str, str, str, str, float, list[str]]] = []
        for (src, tgt), occurrences in rel_map.items():
            existing = storage.graph.get_edge(src, tgt)

            all_keywords = set()
            descriptions = []
            weight = 0.0
            for rel, _ in occurrences:
                if rel.keywords:
                    all_keywords.update(k.strip() for k in rel.keywords.split(",") if k.strip())
                if rel.description:
                    descriptions.append(rel.description)
                weight += 1.0

            if existing:
                if existing.get("description"):
                    descriptions.insert(0, existing["description"])
                old_kw = existing.get("keywords", "")
                if old_kw:
                    all_keywords.update(k.strip() for k in old_kw.split(",") if k.strip())
                weight += existing.get("weight", 0.0)

            merged_desc = await self._merge_descriptions_async(descriptions, namespace=namespace)
            keywords_str = ", ".join(sorted(all_keywords))
            chunk_ids = [cid for _, cid in occurrences]
            prepared.append((src, tgt, merged_desc, keywords_str, weight, chunk_ids))

        embed_texts = [
            f"{kw}\t{src}\n{tgt}\n{desc}"
            for src, tgt, desc, kw, _, _ in prepared
        ]
        if embed_texts:
            vectors = await self.embedding.embed_batch(
                embed_texts,
                call_context=dict(operation="relation_embedding", namespace=namespace),
            )
        else:
            vectors = []

        for (src, tgt, merged_desc, keywords_str, weight, chunk_ids), vector in zip(prepared, vectors):
            storage.graph.upsert_edge(
                src, tgt, weight=weight, description=merged_desc, keywords=keywords_str,
            )
            storage.vectors.relationships.add(
                f"{src}||{tgt}", vector, {"src": src, "tgt": tgt, "keywords": keywords_str},
            )
            storage.sqlite.upsert_relation_chunks(src, tgt, chunk_ids)

        return len(rel_map)

    def _merge_descriptions(self, descriptions: list[str]) -> str:
        unique = list(dict.fromkeys(d for d in descriptions if d))
        if not unique:
            return ""
        if len(unique) <= 3:
            return " | ".join(unique)
        return " | ".join(unique[:3]) + f" (+{len(unique) - 3} more)"

    async def _merge_descriptions_async(self, descriptions: list[str], namespace: str = "") -> str:
        unique = list(dict.fromkeys(d for d in descriptions if d))
        if not unique:
            return ""
        if not self.config.enable_llm_summary or not self.llm:
            return self._merge_descriptions(descriptions)
        if len(unique) < self.config.llm_summary_min_descriptions:
            return " | ".join(unique)
        try:
            prompt = SUMMARY_PROMPT.format(descriptions="\n---\n".join(unique))
            ctx = dict(operation="description_summary", namespace=namespace)
            result = await self.llm.complete(prompt, model_type="enrichment", call_context=ctx)
            return result
        except Exception as e:
            logger.warning("LLM summary failed, falling back to concat: %s", e)
            return self._merge_descriptions(descriptions)
