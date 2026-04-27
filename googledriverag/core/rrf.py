from __future__ import annotations


def rrf_merge_chunks(
    ranked_lists: list[list[dict]],
    k: int = 60,
    id_key: str = "chunk_id",
) -> list[dict]:
    scores: dict[str, float] = {}
    data: dict[str, dict] = {}

    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            cid = item.get(id_key, "")
            if not cid:
                continue
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
            if cid not in data:
                data[cid] = item

    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    result = []
    for cid in sorted_ids:
        chunk = dict(data[cid])
        chunk["score"] = scores[cid]
        result.append(chunk)
    return result


def rrf_merge_multi_namespace(
    ns_results: list[dict],
    k: int = 60,
) -> dict:
    all_chunk_lists = []
    all_entities = []
    all_relations = []

    for ns_result in ns_results:
        chunks = ns_result.get("chunks", [])
        for c in chunks:
            if "namespace" not in c:
                c["namespace"] = ns_result.get("namespace", "")
        all_chunk_lists.append(chunks)
        all_entities.extend(ns_result.get("entities", []))
        all_relations.extend(ns_result.get("relations", []))

    merged_chunks = rrf_merge_chunks(all_chunk_lists, k=k)

    seen_entities = set()
    unique_entities = []
    for e in all_entities:
        key = e.get("name", "")
        if key not in seen_entities:
            seen_entities.add(key)
            unique_entities.append(e)

    seen_rels = set()
    unique_relations = []
    for r in all_relations:
        key = (r.get("source", ""), r.get("target", ""))
        sorted_key = tuple(sorted(key))
        if sorted_key not in seen_rels:
            seen_rels.add(sorted_key)
            unique_relations.append(r)

    return {
        "chunks": merged_chunks,
        "entities": unique_entities,
        "relations": unique_relations,
    }
