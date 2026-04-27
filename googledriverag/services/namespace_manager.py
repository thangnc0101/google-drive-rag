from __future__ import annotations

import json
from pathlib import Path

from googledriverag.config import AppConfig, NamespaceConfig
from googledriverag.storage.namespace_storage import NamespaceStorage


class NamespaceNotFoundError(Exception):
    pass


class NamespaceManager:
    def __init__(self, config: AppConfig):
        self.config = config
        self.data_dir = config.storage.data_dir
        self.dim = config.embedding.dimensions
        self._storages: dict[str, NamespaceStorage] = {}
        self._configs: dict[str, NamespaceConfig] = {}

    def init_from_config(self):
        for ns in self.config.namespaces:
            self._configs[ns.name] = ns
            base = Path(self.data_dir) / "namespaces" / ns.name
            base.mkdir(parents=True, exist_ok=True)

    def get_storage(self, name: str) -> NamespaceStorage:
        if name not in self._configs:
            raise NamespaceNotFoundError(f"Namespace '{name}' not found")
        if name not in self._storages:
            storage = NamespaceStorage(name, self.data_dir, self.dim)
            storage.load()
            self._storages[name] = storage
        return self._storages[name]

    def get_config(self, name: str) -> NamespaceConfig:
        if name not in self._configs:
            raise NamespaceNotFoundError(f"Namespace '{name}' not found")
        return self._configs[name]

    def create(self, name: str, description: str = "", folder_ids: list[str] | None = None):
        if name in self._configs:
            raise ValueError(f"Namespace '{name}' already exists")
        ns_config = NamespaceConfig(name=name, description=description, folder_ids=folder_ids or [])
        self._configs[name] = ns_config
        base = Path(self.data_dir) / "namespaces" / name
        base.mkdir(parents=True, exist_ok=True)

    def update(self, name: str, description: str | None = None, folder_ids: list[str] | None = None):
        if name not in self._configs:
            raise NamespaceNotFoundError(f"Namespace '{name}' not found")
        ns = self._configs[name]
        if description is not None:
            ns.description = description
        if folder_ids is not None:
            ns.folder_ids = folder_ids

    def delete(self, name: str) -> int:
        if name not in self._configs:
            raise NamespaceNotFoundError(f"Namespace '{name}' not found")
        docs_removed = 0
        if name in self._storages:
            storage = self._storages[name]
            docs_removed = len(storage.sqlite.list_documents())
            storage.destroy()
            del self._storages[name]
        else:
            storage = NamespaceStorage(name, self.data_dir, self.dim)
            storage.destroy()
        del self._configs[name]
        return docs_removed

    def list_all_names(self) -> list[str]:
        return list(self._configs.keys())

    def list_all_details(self) -> list[dict]:
        results = []
        for name, cfg in self._configs.items():
            info = {
                "name": name,
                "description": cfg.description,
                "folder_ids": cfg.folder_ids,
                "status": "active",
            }
            try:
                storage = self.get_storage(name)
                stats = storage.sqlite.get_stats()
                graph_stats = storage.graph.get_stats()
                info.update(stats)
                info.update(graph_stats)
            except Exception:
                pass
            results.append(info)
        return results

    def get_detail(self, name: str) -> dict:
        if name not in self._configs:
            raise NamespaceNotFoundError(f"Namespace '{name}' not found")
        cfg = self._configs[name]
        storage = self.get_storage(name)
        stats = storage.sqlite.get_stats()
        graph_stats = storage.graph.get_stats()
        return {
            "name": name,
            "description": cfg.description,
            "folder_ids": cfg.folder_ids,
            "status": "active",
            **stats,
            **graph_stats,
        }

    def close_all(self):
        for s in self._storages.values():
            s.unload()
        self._storages.clear()
