from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from googledriverag.dependencies import get_namespace_manager, verify_auth
from googledriverag.models.namespace import (
    CreateNamespaceRequest,
    NamespaceDeleteResponse,
    NamespaceDetail,
    NamespaceListItem,
    NamespaceResponse,
    UpdateNamespaceRequest,
)
from googledriverag.services.namespace_manager import NamespaceManager

router = APIRouter(tags=["namespaces"], dependencies=[Depends(verify_auth)])


@router.post("/", response_model=NamespaceResponse)
async def create_namespace(
    req: CreateNamespaceRequest,
    ns_mgr: NamespaceManager = Depends(get_namespace_manager),
):
    ns_mgr.create(req.name, req.description, req.folder_ids)
    return NamespaceResponse(name=req.name, status="created")


@router.get("/", response_model=list[NamespaceListItem])
async def list_namespaces(
    ns_mgr: NamespaceManager = Depends(get_namespace_manager),
):
    details = ns_mgr.list_all_details()
    return [NamespaceListItem(**d) for d in details]


@router.get("/{name}", response_model=NamespaceDetail)
async def get_namespace(
    name: str,
    ns_mgr: NamespaceManager = Depends(get_namespace_manager),
):
    detail = ns_mgr.get_detail(name)
    return NamespaceDetail(**detail)


@router.put("/{name}", response_model=NamespaceResponse)
async def update_namespace(
    name: str,
    req: UpdateNamespaceRequest,
    ns_mgr: NamespaceManager = Depends(get_namespace_manager),
):
    ns_mgr.update(name, req.description, req.folder_ids)
    return NamespaceResponse(name=name, status="updated")


@router.delete("/{name}", response_model=NamespaceDeleteResponse)
async def delete_namespace(
    name: str,
    ns_mgr: NamespaceManager = Depends(get_namespace_manager),
):
    docs_removed = ns_mgr.delete(name)
    return NamespaceDeleteResponse(name=name, documents_removed=docs_removed)


@router.post("/{name}/sync")
async def sync_namespace(name: str, request: Request = None):
    if not hasattr(request.app.state, "sync_service"):
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Sync service not configured (missing Google Drive credentials)")
    sync_svc = request.app.state.sync_service
    result = await sync_svc.sync_namespace(name)
    return {"namespace": result.namespace, "added": result.added, "updated": result.updated, "deleted": result.deleted}
