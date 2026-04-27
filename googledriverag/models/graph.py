from __future__ import annotations

from pydantic import BaseModel


class EntityListItem(BaseModel):
    name: str = ""
    type: str = ""
    namespace: str = ""
    description: str = ""
    chunk_count: int = 0


class RelationshipItem(BaseModel):
    source: str = ""
    target: str = ""
    relation: str = ""
    namespace: str = ""
    description: str = ""
