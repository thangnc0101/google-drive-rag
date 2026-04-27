from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from googledriverag.dependencies import get_namespace_manager, verify_auth
from googledriverag.models.document import DocumentDeleteResponse, DocumentListItem
from googledriverag.services.namespace_manager import NamespaceManager

router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(verify_auth)])


@router.post("/sync")
async def sync_all(request: Request):
    if not hasattr(request.app.state, "sync_service"):
        raise HTTPException(status_code=503, detail="Sync service not configured")
    sync_svc = request.app.state.sync_service
    results = await sync_svc.sync_all()
    return {
        "results": [
            {"namespace": r.namespace, "added": r.added, "updated": r.updated, "deleted": r.deleted}
            for r in results
        ]
    }


@router.get("/", response_model=list[DocumentListItem])
async def list_documents(
    namespace: str = Query(None),
    ns_mgr: NamespaceManager = Depends(get_namespace_manager),
):
    namespaces = [namespace] if namespace else ns_mgr.list_all_names()
    results = []
    for ns in namespaces:
        try:
            storage = ns_mgr.get_storage(ns)
            docs = storage.sqlite.list_documents()
            for doc in docs:
                results.append(DocumentListItem(
                    id=doc.doc_id, name=doc.name, namespace=ns,
                    status=doc.status, chunks=storage.sqlite.count_chunks_by_doc(doc.doc_id),
                    last_synced=doc.synced_at, url=doc.url, file_size=doc.file_size,
                ))
        except Exception:
            pass
    return results


@router.post("/{doc_id}/reindex")
async def reindex_document(
    doc_id: str,
    namespace: str = Query(...),
    request: Request = None,
    ns_mgr: NamespaceManager = Depends(get_namespace_manager),
):
    if not hasattr(request.app.state, "sync_service"):
        raise HTTPException(status_code=503, detail="Sync service not configured (missing Google Drive credentials)")
    storage = ns_mgr.get_storage(namespace)
    doc = storage.sqlite.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc.drive_file_id:
        raise HTTPException(status_code=400, detail="Document has no Drive file ID, cannot reindex")

    sync_svc = request.app.state.sync_service
    sync_svc._delete_document(storage, doc_id)

    drive_file = sync_svc.drive.get_file_metadata(doc.drive_file_id)
    if not drive_file:
        raise HTTPException(status_code=404, detail="File no longer exists on Google Drive")

    await sync_svc._ingest_file(storage, drive_file)
    storage.graph.detect_communities()
    storage.graph.save()

    new_doc = storage.sqlite.get_document_by_drive_id(doc.drive_file_id)
    chunks_count = storage.sqlite.count_chunks_by_doc(new_doc.doc_id) if new_doc else 0
    return {
        "id": new_doc.doc_id if new_doc else doc_id,
        "name": doc.name,
        "namespace": namespace,
        "chunks": chunks_count,
        "status": "reindexed",
        "url": new_doc.url if new_doc else "",
    }


@router.delete("/{doc_id}", response_model=DocumentDeleteResponse)
async def delete_document(
    doc_id: str,
    namespace: str = Query(...),
    ns_mgr: NamespaceManager = Depends(get_namespace_manager),
):
    storage = ns_mgr.get_storage(namespace)
    doc = storage.sqlite.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    # Collect chunk ids first
    chunk_ids = storage.sqlite.conn.execute(
        "SELECT chunk_id FROM chunks WHERE doc_id = ?", (doc_id,)
    ).fetchall()
    chunk_ids = [r["chunk_id"] for r in chunk_ids]
    # Find orphaned entities/relationships before deleting mappings
    orphaned_entities, orphaned_relations = storage.sqlite.find_orphaned_after_chunk_delete(chunk_ids)
    # Delete chunks (also deletes mappings)
    storage.sqlite.delete_chunks_by_doc(doc_id)
    for cid in chunk_ids:
        storage.vectors.chunks.delete(cid)
    # Remove orphaned entities from graph + vector store
    for name in orphaned_entities:
        storage.graph.remove_node(name)
        storage.vectors.entities.delete(name)
    # Remove orphaned relationships from graph + vector store
    for src, tgt in orphaned_relations:
        storage.graph.remove_edge(src, tgt)
        rel_key = f"{src}||{tgt}"
        storage.vectors.relationships.delete(rel_key)
    storage.sqlite.delete_document_row(doc_id)
    storage.vectors.save()
    storage.graph.detect_communities()
    storage.graph.save()
    return DocumentDeleteResponse(
        id=doc_id,
        chunks_removed=len(chunk_ids),
        entities_removed=len(orphaned_entities),
    )
