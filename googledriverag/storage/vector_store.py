from __future__ import annotations

import json
from pathlib import Path

import hnswlib


class HnswIndex:
    def __init__(self, index_path: Path, meta_path: Path, dim: int):
        self.index_path = index_path
        self.meta_path = meta_path
        self.dim = dim
        self.index: hnswlib.Index | None = None
        self.metadata: dict[int, dict] = {}
        self.id_to_internal: dict[str, int] = {}
        self.next_id: int = 0

    def load(self):
        if self.index_path.exists() and self.meta_path.exists():
            self.index = hnswlib.Index(space="cosine", dim=self.dim)
            self.index.load_index(str(self.index_path))
            self.index.set_ef(50)
            raw_meta = json.loads(self.meta_path.read_text())
            self.metadata = {int(k): v for k, v in raw_meta.items()}
            self._rebuild_id_map()
        else:
            self._init_empty()

    def _init_empty(self):
        self.index = hnswlib.Index(space="cosine", dim=self.dim)
        self.index.init_index(max_elements=1000, ef_construction=200, M=16)
        self.index.set_ef(50)
        self.metadata = {}
        self.id_to_internal = {}
        self.next_id = 0

    def _rebuild_id_map(self):
        self.id_to_internal = {}
        max_id = -1
        for internal_id, meta in self.metadata.items():
            ext_id = meta.get("id", "")
            self.id_to_internal[ext_id] = internal_id
            if internal_id > max_id:
                max_id = internal_id
        self.next_id = max_id + 1 if max_id >= 0 else 0

    def add(self, external_id: str, vector: list[float], meta: dict | None = None):
        if external_id in self.id_to_internal:
            self.delete(external_id)
        if self.next_id >= self.index.get_max_elements():
            new_size = max(self.index.get_max_elements() * 2, self.next_id + 100)
            self.index.resize_index(new_size)
        self.index.add_items([vector], [self.next_id])
        self.metadata[self.next_id] = {"id": external_id, **(meta or {})}
        self.id_to_internal[external_id] = self.next_id
        self.next_id += 1

    def delete(self, external_id: str):
        if external_id in self.id_to_internal:
            internal_id = self.id_to_internal.pop(external_id)
            self.index.mark_deleted(internal_id)
            self.metadata.pop(internal_id, None)

    def query(self, vector: list[float], top_k: int = 10, threshold: float = 0.0) -> list[dict]:
        if not self.id_to_internal:
            return []
        k = min(top_k, len(self.id_to_internal))
        if k == 0:
            return []
        labels, distances = self.index.knn_query([vector], k=k)
        results = []
        for label, dist in zip(labels[0], distances[0]):
            score = 1.0 - dist
            if score < threshold:
                continue
            meta = self.metadata.get(int(label))
            if meta:
                results.append({"score": score, **meta})
        return results

    def save(self):
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        if self.index is not None:
            self.index.save_index(str(self.index_path))
            serializable = {str(k): v for k, v in self.metadata.items()}
            self.meta_path.write_text(json.dumps(serializable, ensure_ascii=False))

    def unload(self):
        if self.index is not None:
            self.save()
        self.index = None
        self.metadata = {}
        self.id_to_internal = {}

    def count(self) -> int:
        return len(self.id_to_internal)


class VectorStore:
    def __init__(self, vectors_dir: Path, dim: int):
        self.chunks = HnswIndex(vectors_dir / "chunks.bin", vectors_dir / "chunks_meta.json", dim)
        self.entities = HnswIndex(vectors_dir / "entities.bin", vectors_dir / "entities_meta.json", dim)
        self.relationships = HnswIndex(
            vectors_dir / "relationships.bin", vectors_dir / "relationships_meta.json", dim
        )

    def load(self):
        self.chunks.load()
        self.entities.load()
        self.relationships.load()

    def save(self):
        self.chunks.save()
        self.entities.save()
        self.relationships.save()

    def unload(self):
        self.chunks.unload()
        self.entities.unload()
        self.relationships.unload()
