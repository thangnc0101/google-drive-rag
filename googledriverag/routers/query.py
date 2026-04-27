from __future__ import annotations

from fastapi import APIRouter, Depends

from googledriverag.dependencies import verify_auth
from googledriverag.models.query import (
    ChunkItem,
    QueryRequest,
    QueryResponse,
    RetrieveRequest,
    RetrieveResponse,
    SourceItem,
)
from googledriverag.services.query_service import QueryService


router = APIRouter(tags=["query"], dependencies=[Depends(verify_auth)])


from fastapi import Request


def _get_query_service(request: Request):
    return request.app.state.query_service


@router.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest, query_svc: QueryService = Depends(_get_query_service)):
    result = await query_svc.query(
        query_text=req.query,
        namespaces=req.namespaces,
        mode=req.mode,
        top_k=req.top_k,
    )
    return QueryResponse(
        answer=result["answer"],
        sources=[SourceItem(**s) for s in result.get("sources", [])],
        chunks=[ChunkItem(**c) for c in result.get("chunks", [])],
    )


@router.post("/query/retrieve", response_model=RetrieveResponse)
async def retrieve(req: RetrieveRequest, query_svc: QueryService = Depends(_get_query_service)):
    result = await query_svc.retrieve_only(
        query_text=req.query,
        namespaces=req.namespaces,
        mode=req.mode,
        top_k=req.top_k,
    )
    return RetrieveResponse(
        chunks=[ChunkItem(**c) for c in result.get("chunks", [])],
    )
