"""In-memory GraphDriver with the same semantics as the Ladybug and Neo4j drivers, for unit tests."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from memory_engine.core.errors import NotFoundError
from memory_engine.database.base import Edge, EdgeTargets, GraphSchema, Node


class InMemoryDriver:
    def __init__(self, id_key: str = "id"):
        self.id_key = id_key
        self.nodes: dict[str, Node] = {}
        self.rels: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.schema: list[str] | None = None

    def close(self) -> None:
        pass

    def ensure_schema(self, schema: GraphSchema) -> None:
        self.schema = schema.labels

    def get_nodes(self, ids: Iterable[str]) -> dict[str, Node]:
        return {i: Node(i, self.nodes[i].cls, dict(self.nodes[i].props)) for i in ids if i in self.nodes}

    def find_nodes(
        self,
        labels: Iterable[str] | None = None,
        filters: Mapping[str, Any] | None = None,
        edge_filters: Mapping[str, str] | None = None,
        text: str | None = None,
        text_keys: Iterable[str] = (),
        exclude: Mapping[str, Any] | None = None,
        limit: int | None = None,
    ) -> list[Node]:
        labels = set(labels) if labels is not None else None
        text_keys = list(text_keys)
        found = []
        for node in self.nodes.values():
            props = node.props
            if labels is not None and node.cls not in labels:
                continue
            if any(props.get(k) != v for k, v in (filters or {}).items()):
                continue
            if any(props.get(k) == v for k, v in (exclude or {}).items()):
                continue
            if any((node.id, rel, dst) not in self.rels for rel, dst in (edge_filters or {}).items()):
                continue
            if text and not any(
                props.get(k) is not None and text.lower() in str(props[k]).lower() for k in text_keys
            ):
                continue
            found.append(Node(node.id, node.cls, dict(props)))
        found.sort(key=lambda n: n.props.get("updated_at") or n.props.get("created_at"), reverse=True)
        return found[:limit] if limit is not None else found

    def edges(
        self,
        from_ids: Iterable[str] | None = None,
        to_ids: Iterable[str] | None = None,
        rel: str | None = None,
    ) -> list[Edge]:
        from_ids = set(from_ids) if from_ids is not None else None
        to_ids = set(to_ids) if to_ids is not None else None
        return [
            Edge(s, r, d, dict(p))
            for (s, r, d), p in self.rels.items()
            if (from_ids is None or s in from_ids) and (to_ids is None or d in to_ids) and (rel is None or r == rel)
        ]

    def _merge_edge(self, src: str, rel: str, dst: str, props: Mapping[str, Any]) -> None:
        if src not in self.nodes or dst not in self.nodes:
            raise NotFoundError(f"Cannot link `{src}` -[{rel}]-> `{dst}`: a node is missing")
        now = datetime.now(UTC)
        current = self.rels.setdefault((src, rel, dst), {"created_at": now})
        current.update(props)
        current["updated_at"] = now

    def _drop_other_edges(self, src: str, rel: str, keep: list[str]) -> None:
        for key in [k for k in self.rels if k[0] == src and k[1] == rel and k[2] not in keep]:
            del self.rels[key]

    def write_node(
        self,
        node_id: str,
        label: str,
        props: Mapping[str, Any],
        create_props: Mapping[str, Any],
        replace_edges: Mapping[str, EdgeTargets],
    ) -> None:
        now = datetime.now(UTC)
        node = self.nodes.get(node_id)
        if node is None:
            node = Node(node_id, label, {self.id_key: node_id, **create_props, "created_at": now})
            self.nodes[node_id] = node
        node.cls = label
        for key, value in props.items():
            if value is None:
                node.props.pop(key, None)
            else:
                node.props[key] = value
        node.props["updated_at"] = now
        for rel, targets in replace_edges.items():
            self._drop_other_edges(node_id, rel, [dst for dst, _ in targets])
            for dst, edge_props in targets:
                self._merge_edge(node_id, rel, dst, edge_props)

    def add_edge(self, src: str, rel: str, dst: str, props: Mapping[str, Any], single: bool) -> None:
        if single:
            self._drop_other_edges(src, rel, [dst])
        self._merge_edge(src, rel, dst, props)

    def remove_edge(self, src: str, rel: str, dst: str) -> bool:
        return self.rels.pop((src, rel, dst), None) is not None
