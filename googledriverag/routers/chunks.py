from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query

from googledriverag.dependencies import get_namespace_manager, verify_auth
from googledriverag.models.chunk import (
    ChunkContextItem,
    ChunkContextResponse,
    ChunkDetail,
    ChunkSiblingsResponse,
)
from googledriverag.services.namespace_manager import NamespaceManager

router = APIRouter(prefix="/chunks", tags=["chunks"], dependencies=[Depends(verify_auth)])


@router.get("/{chunk_id}", response_model=ChunkDetail)
async def get_chunk(
    chunk_id: str,
    namespace: str = Query(...),
    ns_mgr: NamespaceManager = Depends(get_namespace_manager),
):
    storage = ns_mgr.get_storage(namespace)
    record = storage.sqlite.get_chunk(chunk_id)
    if not record:
        raise HTTPException(status_code=404, detail="Chunk not found")
    siblings = storage.sqlite.get_chunks_by_doc(record.doc_id)
    total = len(siblings)
    keywords = json.loads(record.keywords) if record.keywords else []
    doc = storage.sqlite.get_document(record.doc_id)
    return ChunkDetail(
        chunk_id=record.chunk_id,
        namespace=namespace,
        document=doc.name if doc else record.doc_id,
        doc_id=record.doc_id,
        sequence_index=record.sequence_index,
        total_chunks_in_doc=total,
        text=record.text,
        page=record.page,
        section=record.section,
        keywords=keywords,
        has_previous=record.sequence_index > 0,
        has_next=record.sequence_index < total - 1,
    )


@router.get("/{chunk_id}/context", response_model=ChunkContextResponse)
async def get_chunk_context(
    chunk_id: str,
    namespace: str = Query(...),
    before: int = Query(2, ge=0, le=20),
    after: int = Query(2, ge=0, le=20),
    ns_mgr: NamespaceManager = Depends(get_namespace_manager),
):
    storage = ns_mgr.get_storage(namespace)
    target = storage.sqlite.get_chunk(chunk_id)
    if not target:
        raise HTTPException(status_code=404, detail="Chunk not found")

    context_chunks = storage.sqlite.get_chunk_context(chunk_id, before, after)
    all_siblings = storage.sqlite.get_chunks_by_doc(target.doc_id)
    total = len(all_siblings)
    doc = storage.sqlite.get_document(target.doc_id)

    items = []
    for c in context_chunks:
        items.append(ChunkContextItem(
            chunk_id=c.chunk_id,
            sequence_index=c.sequence_index,
            text=c.text,
            page=c.page,
            section=c.section,
            is_target=(c.chunk_id == chunk_id),
        ))

    has_more_before = target.sequence_index - before > 0
    has_more_after = target.sequence_index + after < total - 1

    return ChunkContextResponse(
        target_chunk_id=chunk_id,
        namespace=namespace,
        document=doc.name if doc else target.doc_id,
        chunks=items,
        has_more_before=has_more_before,
        has_more_after=has_more_after,
    )


@router.get("/{chunk_id}/siblings", response_model=ChunkSiblingsResponse)
async def get_chunk_siblings(
    chunk_id: str,
    namespace: str = Query(...),
    ns_mgr: NamespaceManager = Depends(get_namespace_manager),
):
    storage = ns_mgr.get_storage(namespace)
    target = storage.sqlite.get_chunk(chunk_id)
    if not target:
        raise HTTPException(status_code=404, detail="Chunk not found")

    siblings = storage.sqlite.get_chunk_siblings(chunk_id)
    doc = storage.sqlite.get_document(target.doc_id)

    items = [
        ChunkContextItem(
            chunk_id=c.chunk_id,
            sequence_index=c.sequence_index,
            text=c.text,
            page=c.page,
            section=c.section,
            is_target=(c.chunk_id == chunk_id),
        )
        for c in siblings
    ]

    return ChunkSiblingsResponse(
        document=doc.name if doc else target.doc_id,
        namespace=namespace,
        total_chunks=len(siblings),
        chunks=items,
    )
