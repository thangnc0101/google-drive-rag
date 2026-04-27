from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class DocumentProgress:
    doc_name: str
    namespace: str
    stage: str = "pending"
    current: int = 0
    total: int = 0
    started_at: float = 0.0

    @property
    def percent(self) -> int:
        if self.total <= 0:
            return 0
        return min(int(self.current / self.total * 100), 100)

    def to_dict(self) -> dict:
        return {
            "doc_name": self.doc_name,
            "namespace": self.namespace,
            "stage": self.stage,
            "current": self.current,
            "total": self.total,
            "percent": self.percent,
            "started_at": self.started_at,
        }


@dataclass
class SyncProgress:
    namespace: str
    file_current: int = 0
    file_total: int = 0
    started_at: float = 0.0
    documents: dict[str, DocumentProgress] = field(default_factory=dict)

    @property
    def percent(self) -> int:
        if self.file_total <= 0:
            return 0
        completed = self.file_current * 100
        for dp in self.documents.values():
            sub = dp.current / dp.total if dp.total > 0 else (1.0 if dp.stage == "done" else 0.0)
            completed += _stage_percent(dp.stage, sub)
        return min(int(completed / self.file_total), 100)

    def to_dict(self) -> dict:
        return {
            "namespace": self.namespace,
            "file_current": self.file_current,
            "file_total": self.file_total,
            "percent": self.percent,
            "started_at": self.started_at,
            "documents": {k: v.to_dict() for k, v in self.documents.items()},
        }


STAGES = [
    "parsing",
    "chunking",
    "enriching",
    "embedding",
    "saving",
    "merging_entities",
    "merging_relationships",
    "done",
]

STAGE_WEIGHTS = {
    "parsing": 5,
    "chunking": 5,
    "enriching": 45,
    "embedding": 20,
    "saving": 5,
    "merging_entities": 10,
    "merging_relationships": 10,
}


def _stage_percent(stage: str, sub_progress: float = 1.0) -> int:
    if stage == "done":
        return 100
    cumulative = 0
    for s, w in STAGE_WEIGHTS.items():
        if s == stage:
            return min(int(cumulative + w * sub_progress), 100)
        cumulative += w
    return 0


class ProgressStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._syncs: dict[str, SyncProgress] = {}
        self._docs: dict[str, DocumentProgress] = {}

    def start_sync(self, namespace: str, file_total: int):
        with self._lock:
            self._syncs[namespace] = SyncProgress(
                namespace=namespace,
                file_total=file_total,
                started_at=time.time(),
            )

    def update_sync_file(self, namespace: str, file_current: int):
        with self._lock:
            sp = self._syncs.get(namespace)
            if sp:
                sp.file_current = file_current

    def finish_sync(self, namespace: str):
        with self._lock:
            self._syncs.pop(namespace, None)

    def start_document(self, doc_key: str, doc_name: str, namespace: str):
        with self._lock:
            dp = DocumentProgress(
                doc_name=doc_name,
                namespace=namespace,
                stage="parsing",
                started_at=time.time(),
            )
            self._docs[doc_key] = dp
            sp = self._syncs.get(namespace)
            if sp:
                sp.documents[doc_key] = dp

    def update_document(self, doc_key: str, stage: str, current: int = 0, total: int = 0):
        with self._lock:
            dp = self._docs.get(doc_key)
            if dp:
                dp.stage = stage
                dp.current = current
                dp.total = total

    def finish_document(self, doc_key: str):
        with self._lock:
            dp = self._docs.pop(doc_key, None)
            if dp:
                sp = self._syncs.get(dp.namespace)
                if sp:
                    sp.documents.pop(doc_key, None)

    def get_all(self) -> dict:
        with self._lock:
            syncs = {k: v.to_dict() for k, v in self._syncs.items()}
            docs = {k: v.to_dict() for k, v in self._docs.items()}
            return {"syncs": syncs, "documents": docs}

    def get_namespace(self, namespace: str) -> dict | None:
        with self._lock:
            sp = self._syncs.get(namespace)
            if sp:
                return sp.to_dict()
            ns_docs = {k: v.to_dict() for k, v in self._docs.items() if v.namespace == namespace}
            if ns_docs:
                return {"namespace": namespace, "documents": ns_docs}
            return None
