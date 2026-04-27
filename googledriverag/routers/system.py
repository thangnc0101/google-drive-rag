from __future__ import annotations

import os
import time

from fastapi import APIRouter, Depends, Request

from googledriverag.dependencies import get_namespace_manager, verify_auth
from googledriverag.services.namespace_manager import NamespaceManager

router = APIRouter(tags=["system"])

_start_time = time.time()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/stats", dependencies=[Depends(verify_auth)])
async def stats(ns_mgr: NamespaceManager = Depends(get_namespace_manager)):
    ns_stats = []
    total_docs = 0
    total_chunks = 0
    for name in ns_mgr.list_all_names():
        try:
            storage = ns_mgr.get_storage(name)
            s = storage.sqlite.get_stats()
            g = storage.graph.get_stats()
            ns_stats.append({
                "name": name,
                "documents": s["document_count"],
                "chunks": s["chunk_count"],
                "entities": g["entity_count"],
                "relationships": g["relationship_count"],
            })
            total_docs += s["document_count"]
            total_chunks += s["chunk_count"]
        except Exception:
            ns_stats.append({"name": name, "documents": 0, "chunks": 0, "entities": 0, "relationships": 0})

    import platform
    import resource
    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if platform.system() == "Linux":
        ram_usage_mb = maxrss / 1024
    else:
        ram_usage_mb = maxrss / (1024 * 1024)

    return {
        "namespaces": ns_stats,
        "total_documents": total_docs,
        "total_chunks": total_chunks,
        "ram_usage_mb": round(ram_usage_mb, 1),
        "uptime_seconds": int(time.time() - _start_time),
    }


@router.get("/progress", dependencies=[Depends(verify_auth)])
async def get_progress(request: Request):
    progress_store = getattr(request.app.state, "progress_store", None)
    if not progress_store:
        return {"syncs": {}, "documents": {}}
    return progress_store.get_all()


@router.post("/sync", dependencies=[Depends(verify_auth)])
async def trigger_sync(request: Request):
    sync_service = getattr(request.app.state, "sync_service", None)
    if not sync_service:
        return {"status": "error", "message": "Google Drive sync not configured"}
    import asyncio
    asyncio.create_task(sync_service.sync_all())
    return {"status": "started"}
