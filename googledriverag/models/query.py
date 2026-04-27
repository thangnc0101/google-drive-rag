from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    query: str
    namespaces: list[str] | None = None
    mode: Literal["local", "global", "hybrid", "mix", "naive", "bypass"] = "hybrid"
    top_k: int = Field(default=5, ge=1, le=50)


class SourceItem(BaseModel):
    document: str = ""
    namespace: str = ""
    chunk_id: str = ""
    page: int | None = None


class ChunkItem(BaseModel):
    chunk_id: str
    namespace: str = ""
    text: str = ""
    score: float = 0.0
    source: str = ""
    sequence_index: int = 0
    has_previous: bool = False
    has_next: bool = False
    page: int | None = None
    section: str | None = None


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceItem] = []
    chunks: list[ChunkItem] = []


class RetrieveRequest(BaseModel):
    query: str
    namespaces: list[str] | None = None
    mode: Literal["local", "global", "hybrid", "mix", "naive", "bypass"] = "hybrid"
    top_k: int = Field(default=10, ge=1, le=50)


class RetrieveResponse(BaseModel):
    chunks: list[ChunkItem] = []
