from __future__ import annotations

import json
from pathlib import Path

import networkx as nx


class GraphStore:
    def __init__(self, graph_dir: Path):
        self.graph_dir = graph_dir
        self.graph_path = graph_dir / "graph.json"
        self.communities_path = graph_dir / "communities.json"
        self.graph: nx.Graph | None = None
        self.communities: dict | None = None

    def load(self):
        if self.graph_path.exists():
            data = json.loads(self.graph_path.read_text())
            self.graph = nx.node_link_graph(data)
        else:
            self.graph = nx.Graph()
        if self.communities_path.exists():
            self.communities = json.loads(self.communities_path.read_text())

    def upsert_node(self, name: str, **attrs):
        self.graph.add_node(name, **attrs)

    def get_node(self, name: str) -> dict | None:
        if name in self.graph:
            return {"name": name, **dict(self.graph.nodes[name])}
        return None

    def get_node_edges(self, name: str) -> list[dict]:
        if name not in self.graph:
            return []
        edges = []
        for u, v, data in self.graph.edges(name, data=True):
            edges.append({"source": u, "target": v, **data})
        return edges

    def get_node_degree(self, name: str) -> int:
        return self.graph.degree(name) if name in self.graph else 0

    def upsert_edge(self, src: str, tgt: str, **attrs):
        key = tuple(sorted([src, tgt]))
        self.graph.add_edge(key[0], key[1], **attrs)

    def get_edge(self, src: str, tgt: str) -> dict | None:
        key = tuple(sorted([src, tgt]))
        if self.graph.has_edge(key[0], key[1]):
            return dict(self.graph.edges[key[0], key[1]])
        return None

    def remove_node(self, name: str):
        if name in self.graph:
            self.graph.remove_node(name)

    def remove_edge(self, src: str, tgt: str):
        key = tuple(sorted([src, tgt]))
        if self.graph.has_edge(key[0], key[1]):
            self.graph.remove_edge(key[0], key[1])

    def list_nodes(self, node_type: str | None = None, limit: int = 50) -> list[dict]:
        results = []
        for name, data in self.graph.nodes(data=True):
            if node_type and data.get("entity_type") != node_type:
                continue
            results.append({"name": name, **data})
            if len(results) >= limit:
                break
        return results

    def detect_communities(self):
        if self.graph.number_of_nodes() == 0:
            self.communities = {}
            return
        try:
            from community import community_louvain
            partition = community_louvain.best_partition(self.graph)
            communities: dict[int, list[str]] = {}
            for node, comm_id in partition.items():
                communities.setdefault(comm_id, []).append(node)
            self.communities = communities
        except ImportError:
            self.communities = {}

    def save(self):
        self.graph_dir.mkdir(parents=True, exist_ok=True)
        if self.graph is not None:
            data = nx.node_link_data(self.graph)
            self.graph_path.write_text(json.dumps(data, ensure_ascii=False))
        if self.communities is not None:
            self.communities_path.write_text(json.dumps(self.communities, ensure_ascii=False))

    def unload(self):
        if self.graph is not None:
            self.save()
        self.graph = None
        self.communities = None

    def get_stats(self) -> dict:
        if self.graph is None:
            return {"entity_count": 0, "relationship_count": 0}
        return {
            "entity_count": self.graph.number_of_nodes(),
            "relationship_count": self.graph.number_of_edges(),
        }
