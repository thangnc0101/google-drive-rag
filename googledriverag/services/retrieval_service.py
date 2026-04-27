from __future__ import annotations

import logging
import time

from googledriverag.config import RetrievalConfig
from googledriverag.core.rrf import rrf_merge_chunks
from googledriverag.services.namespace_manager import NamespaceManager
from googledriverag.storage.namespace_storage import NamespaceStorage

logger = logging.getLogger(__name__)


class RetrievalService:
    def __init__(self, ns_manager: NamespaceManager, config: RetrievalConfig):
        self.ns_manager = ns_manager
        self.config = config

    def search(
        self,
        ns_name: str,
        mode: str,
        query_vec: list[float],
        ll_vec: list[float],
        hl_vec: list[float],
        query_text: str,
        top_k: int = 5,
    ) -> dict:
        t_search = time.perf_counter()
        storage = self.ns_manager.get_storage(ns_name)

        if mode == "local":
            result = self._local_search(storage, ns_name, ll_vec, query_text, top_k)
        elif mode == "global":
            result = self._global_search(storage, ns_name, hl_vec, query_text, top_k)
        elif mode == "hybrid":
            local = self._local_search(storage, ns_name, ll_vec, query_text, top_k)
            global_ = self._global_search(storage, ns_name, hl_vec, query_text, top_k)
            result = self._merge_local_global(ns_name, local, global_)
        elif mode == "mix":
            local = self._local_search(storage, ns_name, ll_vec, query_text, top_k)
            global_ = self._global_search(storage, ns_name, hl_vec, query_text, top_k)
            vector = self._vector_search(storage, ns_name, query_vec, top_k)
            merged = self._merge_local_global(ns_name, local, global_)
            merged["chunks"] = rrf_merge_chunks(
                [merged["chunks"], vector["chunks"]], k=self.config.rrf_k
            )
            result = merged
        elif mode == "naive":
            result = self._vector_search(storage, ns_name, query_vec, top_k)
        else:
            result = self._local_search(storage, ns_name, ll_vec, query_text, top_k)

        logger.debug("[TIMING] retrieval.search ns=%s mode=%s: %.3fs", ns_name, mode, time.perf_counter() - t_search)
        return result

    def _local_search(
        self, storage: NamespaceStorage, ns_name: str,
        ll_vec: list[float], query_text: str, top_k: int,
    ) -> dict:
        t0 = time.perf_counter()
        entity_hits = storage.vectors.entities.query(ll_vec, top_k=top_k)
        logger.debug("[TIMING] local.entity_vector_search: %.3fs", time.perf_counter() - t0)

        entities = []
        relations = []
        entity_chunk_map: dict[str, list[str]] = {}

        t0 = time.perf_counter()
        for hit in entity_hits:
            name = hit.get("name", hit.get("id", ""))
            node = storage.graph.get_node(name)
            if node:
                node["score"] = hit["score"]
                node["namespace"] = ns_name
                entities.append(node)

            edges = storage.graph.get_node_edges(name)
            for edge in edges:
                edge["namespace"] = ns_name
            relations.extend(edges)

            ids = storage.sqlite.get_entity_chunk_ids(name)
            if ids:
                entity_chunk_map[name] = ids
        logger.debug("[TIMING] local.graph_traversal: %.3fs", time.perf_counter() - t0)

        seen_rels = set()
        unique_relations = []
        for r in relations:
            key = tuple(sorted([r["source"], r["target"]]))
            if key not in seen_rels:
                seen_rels.add(key)
                unique_relations.append(r)

        kg_chunk_ids = self._weighted_chunk_selection(entity_chunk_map, top_k)

        t0 = time.perf_counter()
        bm25_hits = storage.sqlite.keyword_search(query_text, limit=top_k)
        logger.debug("[TIMING] local.bm25_search: %.3fs", time.perf_counter() - t0)
        bm25_chunk_ids = [cid for cid, _ in bm25_hits]

        t0 = time.perf_counter()
        kg_chunks = self._collect_chunks(storage, ns_name, kg_chunk_ids)
        bm25_chunks = self._collect_chunks(storage, ns_name, bm25_chunk_ids)
        chunks = rrf_merge_chunks([kg_chunks, bm25_chunks], k=self.config.rrf_k)
        logger.debug("[TIMING] local.collect_chunks: %.3fs", time.perf_counter() - t0)

        return {"namespace": ns_name, "entities": entities, "relations": unique_relations, "chunks": chunks}

    def _global_search(
        self, storage: NamespaceStorage, ns_name: str,
        hl_vec: list[float], query_text: str, top_k: int,
    ) -> dict:
        t0 = time.perf_counter()
        rel_hits = storage.vectors.relationships.query(hl_vec, top_k=top_k)
        logger.debug("[TIMING] global.rel_vector_search: %.3fs", time.perf_counter() - t0)

        relations = []
        entity_names = set()
        rel_chunk_map: dict[str, list[str]] = {}

        t0 = time.perf_counter()
        for hit in rel_hits:
            src = hit.get("src", "")
            tgt = hit.get("tgt", "")
            edge = storage.graph.get_edge(src, tgt)
            if edge:
                edge["source"] = src
                edge["target"] = tgt
                edge["score"] = hit["score"]
                edge["namespace"] = ns_name
                relations.append(edge)
            entity_names.update([src, tgt])

            ids = storage.sqlite.get_relation_chunk_ids(src, tgt)
            if ids:
                rel_key = f"{src}||{tgt}"
                rel_chunk_map[rel_key] = ids

        if self.config.enable_community_search and storage.graph.communities:
            community_entities = self._get_community_entities(storage, entity_names)
            entity_names.update(community_entities)

        entities = []
        for name in entity_names:
            node = storage.graph.get_node(name)
            if node:
                node["namespace"] = ns_name
                entities.append(node)
        logger.debug("[TIMING] global.graph_traversal: %.3fs", time.perf_counter() - t0)

        kg_chunk_ids = self._weighted_chunk_selection(rel_chunk_map, top_k)

        t0 = time.perf_counter()
        chunks = self._collect_chunks(storage, ns_name, kg_chunk_ids)
        logger.debug("[TIMING] global.collect_chunks: %.3fs", time.perf_counter() - t0)

        return {"namespace": ns_name, "entities": entities, "relations": relations, "chunks": chunks}

    def _get_community_entities(
        self, storage: NamespaceStorage, seed_entities: set[str],
    ) -> set[str]:
        communities = storage.graph.communities
        if not communities:
            return set()
        relevant_community_ids = set()
        for comm_id, members in communities.items():
            if seed_entities & set(members):
                relevant_community_ids.add(comm_id)
        extra = set()
        for comm_id in relevant_community_ids:
            extra.update(communities[comm_id])
        return extra - seed_entities

    def _vector_search(
        self, storage: NamespaceStorage, ns_name: str,
        query_vec: list[float], top_k: int,
    ) -> dict:
        t0 = time.perf_counter()
        hits = storage.vectors.chunks.query(query_vec, top_k=top_k)
        logger.debug("[TIMING] naive.chunk_vector_search: %.3fs", time.perf_counter() - t0)
        chunks = []
        doc_totals: dict[str, int] = {}
        t0 = time.perf_counter()
        for hit in hits:
            chunk_id = hit.get("id", "")
            record = storage.sqlite.get_chunk(chunk_id)
            if record:
                if record.doc_id not in doc_totals:
                    doc_totals[record.doc_id] = len(storage.sqlite.get_chunks_by_doc(record.doc_id))
                chunks.append(self._chunk_to_dict(record, ns_name, hit["score"], total_in_doc=doc_totals[record.doc_id]))
        logger.debug("[TIMING] naive.collect_chunks: %.3fs", time.perf_counter() - t0)
        return {"namespace": ns_name, "entities": [], "relations": [], "chunks": chunks}

    def _merge_local_global(self, ns_name: str, local: dict, global_: dict) -> dict:
        all_entities = local["entities"] + global_["entities"]
        seen_e = set()
        unique_entities = []
        for e in all_entities:
            if e["name"] not in seen_e:
                seen_e.add(e["name"])
                unique_entities.append(e)

        all_rels = local["relations"] + global_["relations"]
        seen_r = set()
        unique_rels = []
        for r in all_rels:
            key = tuple(sorted([r["source"], r["target"]]))
            if key not in seen_r:
                seen_r.add(key)
                unique_rels.append(r)

        merged_chunks = rrf_merge_chunks(
            [local["chunks"], global_["chunks"]], k=self.config.rrf_k
        )

        return {"namespace": ns_name, "entities": unique_entities, "relations": unique_rels, "chunks": merged_chunks}

    def _collect_chunks(
        self, storage: NamespaceStorage, ns_name: str, chunk_ids: list[str],
    ) -> list[dict]:
        seen = set()
        chunks = []
        doc_totals: dict[str, int] = {}
        for cid in chunk_ids:
            if cid in seen:
                continue
            seen.add(cid)
            record = storage.sqlite.get_chunk(cid)
            if record:
                if record.doc_id not in doc_totals:
                    doc_totals[record.doc_id] = len(storage.sqlite.get_chunks_by_doc(record.doc_id))
                chunks.append(self._chunk_to_dict(record, ns_name, total_in_doc=doc_totals[record.doc_id]))
        return chunks

    def _weighted_chunk_selection(
        self, source_chunk_map: dict[str, list[str]], top_k: int,
        min_per_source: int = 1,
    ) -> list[str]:
        if not source_chunk_map:
            return []

        chunk_freq: dict[str, int] = {}
        for chunk_ids in source_chunk_map.values():
            for cid in chunk_ids:
                chunk_freq[cid] = chunk_freq.get(cid, 0) + 1

        n_sources = len(source_chunk_map)
        total_budget = max(top_k, n_sources * min_per_source)

        weights = []
        for i in range(n_sources):
            weights.append(max(1.0, n_sources - i))
        weight_sum = sum(weights)

        selected: list[str] = []
        selected_set: set[str] = set()

        for idx, (source_key, chunk_ids) in enumerate(source_chunk_map.items()):
            quota = max(min_per_source, int(weights[idx] / weight_sum * total_budget))
            ranked = sorted(chunk_ids, key=lambda c: chunk_freq.get(c, 0), reverse=True)
            count = 0
            for cid in ranked:
                if cid in selected_set:
                    continue
                selected.append(cid)
                selected_set.add(cid)
                count += 1
                if count >= quota:
                    break

        return selected

    def _chunk_to_dict(
        self, record, ns_name: str, score: float = 0.0,
        total_in_doc: int | None = None,
    ) -> dict:
        has_next = False
        if total_in_doc is not None:
            has_next = record.sequence_index < total_in_doc - 1
        return {
            "chunk_id": record.chunk_id,
            "namespace": ns_name,
            "doc_id": record.doc_id,
            "text": record.text,
            "score": score,
            "source": record.doc_id,
            "sequence_index": record.sequence_index,
            "page": record.page,
            "section": record.section,
            "has_previous": record.sequence_index > 0,
            "has_next": has_next,
        }
