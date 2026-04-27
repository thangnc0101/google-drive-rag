from __future__ import annotations

from pydantic import BaseModel


class ChunkDetail(BaseModel):
    chunk_id: str
    namespace: str = ""
    document: str = ""
    doc_id: str = ""
    sequence_index: int = 0
    total_chunks_in_doc: int = 0
    text: str = ""
    page: int | None = None
    section: str | None = None
    keywords: list[str] = []
    has_previous: bool = False
    has_next: bool = False


class ChunkContextItem(BaseModel):
    chunk_id: str
    sequence_index: int = 0
    text: str = ""
    page: int | None = None
    section: str | None = None
    is_target: bool = False


class ChunkContextResponse(BaseModel):
    target_chunk_id: str
    namespace: str = ""
    document: str = ""
    chunks: list[ChunkContextItem] = []
    has_more_before: bool = False
    has_more_after: bool = False


class ChunkSiblingsResponse(BaseModel):
    document: str = ""
    namespace: str = ""
    total_chunks: int = 0
    chunks: list[ChunkContextItem] = []
