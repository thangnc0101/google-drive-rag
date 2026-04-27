from __future__ import annotations

from pydantic import BaseModel


class DocumentListItem(BaseModel):
    id: str = ""
    name: str = ""
    namespace: str = ""
    status: str = "indexed"
    chunks: int = 0
    last_synced: str = ""
    url: str = ""
    file_size: int = 0


class DocumentDeleteResponse(BaseModel):
    id: str = ""
    status: str = "deleted"
    chunks_removed: int = 0
    entities_removed: int = 0


class SyncResultResponse(BaseModel):
    namespace: str = ""
    added: int = 0
    updated: int = 0
    deleted: int = 0


class SyncAllResponse(BaseModel):
    results: list[SyncResultResponse] = []
