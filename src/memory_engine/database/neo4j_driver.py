"""Neo4j implementation of GraphDriver using dynamically built, parameterized Cypher.

Neo4j cannot parameterize labels or relationship types, so those are interpolated,
but only after `_ident` checks they are plain identifiers, and they are backtick-quoted.
Property keys are never interpolated: filters use dynamic access (`n[k]`) on parameter maps.
Native driver errors are translated into the driver-neutral DatabaseError family.

Install with the `neo4j` extra: `pip install 'memory-engine[neo4j]'`.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterable, Mapping
from typing import Any, TypeVar

from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, DriverError, Neo4jError, ServiceUnavailable

from ..core.errors import DatabaseError, DatabaseUnavailableError, NotFoundError
from .base import Edge, EdgeTargets, GraphSchema, Node
from .base import ident as _ident

BASE_LABEL = "Node"
F = TypeVar("F", bound=Callable[..., Any])


class DatabaseAuthError(DatabaseUnavailableError):
    code = "database_auth"


def _translated(fn: F) -> F:
    """Re-raise native neo4j / socket errors as DatabaseError so callers stay backend-neutral."""

    @functools.wraps(fn)
    def wrapper(self: Neo4jDynamicDriver, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(self, *args, **kwargs)
        except (ServiceUnavailable, OSError) as exc:
            raise DatabaseUnavailableError(
                f"Cannot reach Neo4j at {self.uri} ({exc}). "
                "Start it with `docker compose up -d` or set NEO4J_URI."
            ) from exc
        except AuthError as exc:
            raise DatabaseAuthError(f"Neo4j rejected the credentials: {exc.message}") from exc
        except (Neo4jError, DriverError) as exc:
            raise DatabaseError(str(exc)) from exc

    return wrapper  # type: ignore[return-value]


def _native(value: Any) -> Any:
    if isinstance(value, list):
        return [_native(v) for v in value]
    if hasattr(value, "to_native"):
        return value.to_native()
    return value


class Neo4jDynamicDriver:
    def __init__(
        self,
        uri: str,
        auth: tuple[str, str],
        database: str | None = None,
        id_key: str = "id",
        **driver_config: Any,
    ):
        self.uri = uri
        self._driver = GraphDatabase.driver(uri, auth=auth, **driver_config)
        self._database = database
        self._id = _ident(id_key)
        self.id_key = id_key

    def close(self) -> None:
        self._driver.close()

    @_translated
    def verify(self) -> None:
        self._driver.verify_connectivity()

    def _session(self):
        return self._driver.session(database=self._database)

    def _read(self, query: str, **params: Any) -> list[Any]:
        with self._session() as session:
            return session.execute_read(lambda tx: list(tx.run(query, **params)))

    def _node(self, raw: Any) -> Node:
        props = {k: _native(v) for k, v in dict(raw).items()}
        cls = next((label for label in raw.labels if label != BASE_LABEL), BASE_LABEL)
        return Node(str(props.get(self.id_key)), cls, props)

    # ---------------------------------------------------------------- schema

    @_translated
    def ensure_schema(self, schema: GraphSchema) -> None:
        """Neo4j is schemaless: only the id constraint and per-label id indexes are created."""
        statements = [
            f"CREATE CONSTRAINT node_id_unique IF NOT EXISTS "
            f"FOR (n:{BASE_LABEL}) REQUIRE n.{self._id} IS UNIQUE"
        ]
        for label in schema.labels:
            index = _ident(f"idx_{label.lower()}_{self.id_key}")
            statements.append(f"CREATE INDEX {index} IF NOT EXISTS FOR (n:{_ident(label)}) ON (n.{self._id})")
        with self._session() as session:
            for statement in statements:
                session.run(statement).consume()

    # ---------------------------------------------------------------- reads

    @_translated
    def get_nodes(self, ids: Iterable[str]) -> dict[str, Node]:
        ids = list(ids)
        if not ids:
            return {}
        records = self._read(f"MATCH (n:{BASE_LABEL}) WHERE n.{self._id} IN $ids RETURN n", ids=ids)
        nodes = (self._node(r["n"]) for r in records)
        return {n.id: n for n in nodes}

    @_translated
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
        params: dict[str, Any] = {"filters": dict(filters or {}), "exclude": dict(exclude or {})}
        where = [
            "all(k IN keys($filters) WHERE n[k] = $filters[k])",
            "none(k IN keys($exclude) WHERE n[k] = $exclude[k])",
        ]
        if labels is not None:
            params["labels"] = list(labels)
            where.append("any(l IN labels(n) WHERE l IN $labels)")
        for i, (rel, dst) in enumerate((edge_filters or {}).items()):
            params[f"ef{i}"] = dst
            where.append(
                f"EXISTS {{ MATCH (n)-[:{_ident(rel)}]->(m:{BASE_LABEL}) WHERE m.{self._id} = $ef{i} }}"
            )
        if text:
            params.update(text=text.lower(), text_keys=list(text_keys))
            where.append(
                "any(k IN $text_keys WHERE n[k] IS NOT NULL AND toLower(toString(n[k])) CONTAINS $text)"
            )
        query = (
            f"MATCH (n:{BASE_LABEL}) WHERE {' AND '.join(where)} "
            "RETURN n ORDER BY coalesce(n.updated_at, n.created_at) DESC"
        )
        if limit is not None:
            query += " LIMIT $limit"
            params["limit"] = int(limit)
        return [self._node(r["n"]) for r in self._read(query, **params)]

    @_translated
    def edges(
        self,
        from_ids: Iterable[str] | None = None,
        to_ids: Iterable[str] | None = None,
        rel: str | None = None,
    ) -> list[Edge]:
        params: dict[str, Any] = {}
        where = []
        if from_ids is not None:
            params["from_ids"] = list(from_ids)
            where.append(f"a.{self._id} IN $from_ids")
        if to_ids is not None:
            params["to_ids"] = list(to_ids)
            where.append(f"b.{self._id} IN $to_ids")
        rel_pattern = f":{_ident(rel)}" if rel else ""
        query = f"MATCH (a:{BASE_LABEL})-[r{rel_pattern}]->(b:{BASE_LABEL})"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += f" RETURN a.{self._id} AS src, type(r) AS rel, b.{self._id} AS dst, properties(r) AS props"
        return [
            Edge(r["src"], r["rel"], r["dst"], {k: _native(v) for k, v in r["props"].items()})
            for r in self._read(query, **params)
        ]

    # ---------------------------------------------------------------- writes

    def _merge_edge(self, tx: Any, src: str, rel: str, dst: str, props: Mapping[str, Any]) -> None:
        record = tx.run(
            f"MATCH (a:{BASE_LABEL} {{{self._id}: $src}}), (b:{BASE_LABEL} {{{self._id}: $dst}}) "
            f"MERGE (a)-[r:{_ident(rel)}]->(b) "
            "ON CREATE SET r.created_at = datetime() "
            "SET r += $props, r.updated_at = datetime() "
            "RETURN count(r) AS c",
            src=src, dst=dst, props=dict(props),
        ).single()
        if not record or record["c"] == 0:
            raise NotFoundError(f"Cannot link `{src}` -[{rel}]-> `{dst}`: a node is missing")

    def _drop_other_edges(self, tx: Any, src: str, rel: str, keep: list[str]) -> None:
        tx.run(
            f"MATCH (a:{BASE_LABEL} {{{self._id}: $src}})-[r:{_ident(rel)}]->(b:{BASE_LABEL}) "
            f"WHERE NOT b.{self._id} IN $keep DELETE r",
            src=src, keep=keep,
        ).consume()

    @_translated
    def write_node(
        self,
        node_id: str,
        label: str,
        props: Mapping[str, Any],
        create_props: Mapping[str, Any],
        replace_edges: Mapping[str, EdgeTargets],
    ) -> None:
        def work(tx: Any) -> None:
            tx.run(
                f"MERGE (n:{BASE_LABEL} {{{self._id}: $id}}) "
                "ON CREATE SET n += $create_props, n.created_at = datetime() "
                f"SET n:{_ident(label)}, n += $props, n.updated_at = datetime()",
                id=node_id, props=dict(props), create_props=dict(create_props),
            ).consume()
            for rel, targets in replace_edges.items():
                self._drop_other_edges(tx, node_id, rel, [dst for dst, _ in targets])
                for dst, edge_props in targets:
                    self._merge_edge(tx, node_id, rel, dst, edge_props)

        with self._session() as session:
            session.execute_write(work)

    @_translated
    def add_edge(self, src: str, rel: str, dst: str, props: Mapping[str, Any], single: bool) -> None:
        def work(tx: Any) -> None:
            if single:
                self._drop_other_edges(tx, src, rel, [dst])
            self._merge_edge(tx, src, rel, dst, props)

        with self._session() as session:
            session.execute_write(work)

    @_translated
    def remove_edge(self, src: str, rel: str, dst: str) -> bool:
        def work(tx: Any) -> int:
            record = tx.run(
                f"MATCH (a:{BASE_LABEL} {{{self._id}: $src}})-[r:{_ident(rel)}]->"
                f"(b:{BASE_LABEL} {{{self._id}: $dst}}) DELETE r RETURN count(r) AS c",
                src=src, dst=dst,
            ).single()
            return record["c"] if record else 0

        with self._session() as session:
            return session.execute_write(work) > 0
