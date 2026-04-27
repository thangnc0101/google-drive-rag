from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from googledriverag.dependencies import get_namespace_manager, verify_auth
from googledriverag.models.graph import EntityListItem, RelationshipItem
from googledriverag.services.namespace_manager import NamespaceManager

router = APIRouter(prefix="/graph", tags=["graph"], dependencies=[Depends(verify_auth)])


@router.get("/entities", response_model=list[EntityListItem])
async def list_entities(
    namespace: str = Query(...),
    type: str = Query(None),
    limit: int = Query(50, ge=1, le=500),
    ns_mgr: NamespaceManager = Depends(get_namespace_manager),
):
    storage = ns_mgr.get_storage(namespace)
    nodes = storage.graph.list_nodes(node_type=type, limit=limit)
    results = []
    for node in nodes:
        chunk_count = len(storage.sqlite.get_entity_chunk_ids(node["name"]))
        results.append(EntityListItem(
            name=node["name"],
            type=node.get("entity_type", ""),
            namespace=namespace,
            description=node.get("description", ""),
            chunk_count=chunk_count,
        ))
    return results


@router.get("/relationships", response_model=list[RelationshipItem])
async def get_relationships(
    namespace: str = Query(...),
    entity: str = Query(None),
    ns_mgr: NamespaceManager = Depends(get_namespace_manager),
):
    storage = ns_mgr.get_storage(namespace)
    if entity:
        edges = storage.graph.get_node_edges(entity)
    else:
        edges = []
        if storage.graph.graph:
            for u, v, data in list(storage.graph.graph.edges(data=True))[:100]:
                edges.append({"source": u, "target": v, **data})
    return [
        RelationshipItem(
            source=e.get("source", ""),
            target=e.get("target", ""),
            relation=e.get("keywords", ""),
            namespace=namespace,
            description=e.get("description", ""),
        )
        for e in edges
    ]
