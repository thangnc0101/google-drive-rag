from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from googledriverag.dependencies import verify_auth
from googledriverag.storage.api_call_store import APICallStore

router = APIRouter(tags=["api-calls"], prefix="/api-calls", dependencies=[Depends(verify_auth)])


def _get_store(request: Request) -> APICallStore:
    return request.app.state.api_call_store


@router.get("/")
async def list_api_calls(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    call_type: str | None = Query(default=None),
    namespace: str | None = Query(default=None),
    store: APICallStore = Depends(_get_store),
):
    calls = store.list_calls(limit=limit, offset=offset, call_type=call_type, namespace=namespace)
    return [
        {
            "id": c.id,
            "call_type": c.call_type,
            "model": c.model,
            "document_name": c.document_name,
            "chunk_id": c.chunk_id,
            "operation": c.operation,
            "input_tokens": c.input_tokens,
            "output_tokens": c.output_tokens,
            "created_at": c.created_at,
            "namespace": c.namespace,
        }
        for c in calls
    ]


@router.get("/stats")
async def api_call_stats(store: APICallStore = Depends(_get_store)):
    return store.get_stats()


@router.delete("/")
async def clear_api_calls(store: APICallStore = Depends(_get_store)):
    count = store.clear()
    return {"deleted": count}
