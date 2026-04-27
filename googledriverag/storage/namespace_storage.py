from __future__ import annotations

import shutil
from pathlib import Path

from googledriverag.storage.graph_store import GraphStore
from googledriverag.storage.sqlite_store import SQLiteStore
from googledriverag.storage.vector_store import VectorStore


class NamespaceStorage:
    def __init__(self, ns_name: str, data_dir: str, embedding_dim: int):
        self.ns_name = ns_name
        self.base_path = Path(data_dir) / "namespaces" / ns_name
        self.sqlite = SQLiteStore(self.base_path / "store.db")
        self.vectors = VectorStore(self.base_path / "vectors", embedding_dim)
        self.graph = GraphStore(self.base_path / "graph")
        self._loaded = False

    def load(self):
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.sqlite.connect()
        self.vectors.load()
        self.graph.load()
        self._loaded = True

    def unload(self):
        if self._loaded:
            self.vectors.unload()
            self.graph.unload()
            self.sqlite.close()
            self._loaded = False

    def ensure_loaded(self):
        if not self._loaded:
            self.load()

    def save(self):
        if self._loaded:
            self.vectors.save()
            self.graph.save()

    def destroy(self):
        self.unload()
        if self.base_path.exists():
            shutil.rmtree(self.base_path)

    @property
    def is_loaded(self) -> bool:
        return self._loaded
