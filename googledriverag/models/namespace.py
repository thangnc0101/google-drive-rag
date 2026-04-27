from __future__ import annotations

from pydantic import BaseModel


class CreateNamespaceRequest(BaseModel):
    name: str
    description: str = ""
    folder_ids: list[str] = []


class UpdateNamespaceRequest(BaseModel):
    description: str | None = None
    folder_ids: list[str] | None = None


class NamespaceResponse(BaseModel):
    name: str
    status: str


class NamespaceListItem(BaseModel):
    name: str
    description: str = ""
    document_count: int = 0
    chunk_count: int = 0
    status: str = "active"


class NamespaceDetail(BaseModel):
    name: str
    description: str = ""
    folder_ids: list[str] = []
    document_count: int = 0
    chunk_count: int = 0
    entity_count: int = 0
    relationship_count: int = 0
    status: str = "active"


class NamespaceDeleteResponse(BaseModel):
    name: str
    status: str = "deleted"
    documents_removed: int = 0
