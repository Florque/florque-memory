"""Storage interface the engine depends on. Implementations: LadybugDriver (embedded, default)
and Neo4jDynamicDriver (server)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

EdgeTargets = list[tuple[str, dict[str, Any]]]  # [(target id, edge properties)]

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def ident(name: str) -> str:
    """Backtick-quote a label, relationship type or property key after checking it is a plain identifier.
    Cypher cannot parameterize these, so they are only ever interpolated through this check."""
    if not _IDENT.match(name):
        raise ValueError(f"Refusing unsafe Cypher identifier {name!r}")
    return f"`{name}`"


COLUMN_TYPES = ("string", "integer", "float", "boolean", "date", "datetime")

# The database identity stamp: one reserved row, invisible to the engine (see core/identity.py).
META_TABLE = "_MemoryMeta"
META_ID = "__meta__"


@dataclass(frozen=True)
class Column:
    type: str = "string"  # one of COLUMN_TYPES
    list: bool = False


@dataclass(frozen=True)
class RelTable:
    pairs: tuple[tuple[str, str], ...]  # (source label, target label)
    props: Mapping[str, Column] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphSchema:
    """What the graph must be able to store, derived from the ontology (core/schema.py).
    Schemaless backends only use the labels; schema-first backends create tables from it."""

    id_key: str
    node_tables: Mapping[str, Mapping[str, Column]]  # label -> columns (includes id_key)
    rel_tables: Mapping[str, RelTable]  # rel type -> FROM/TO pairs and edge columns

    @property
    def labels(self) -> list[str]:
        return list(self.node_tables)


@dataclass
class Node:
    id: str
    cls: str
    props: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"class": self.cls, **self.props}


@dataclass
class Edge:
    src: str
    rel: str
    dst: str
    props: dict[str, Any]


class GraphDriver(Protocol):
    """All labels, relationship types and keys passed in have been validated by the engine."""

    def ensure_schema(self, schema: GraphSchema) -> None:
        """Create (idempotently) whatever the backend needs to store this schema."""
        ...

    def get_nodes(self, ids: Iterable[str]) -> dict[str, Node]: ...

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
        """Nodes carrying any of `labels`, matching all `filters`, having an outgoing
        `rel -> target id` for each `edge_filters` entry, containing `text` in any of
        `text_keys`, and matching none of `exclude`. Most recently updated first."""
        ...

    def edges(
        self,
        from_ids: Iterable[str] | None = None,
        to_ids: Iterable[str] | None = None,
        rel: str | None = None,
    ) -> list[Edge]: ...

    def write_node(
        self,
        node_id: str,
        label: str,
        props: Mapping[str, Any],
        create_props: Mapping[str, Any],
        replace_edges: Mapping[str, EdgeTargets],
    ) -> None:
        """Atomically upsert a node. `None` values in `props` remove the property.
        For each rel type in `replace_edges`, outgoing edges of that type become exactly the given targets."""
        ...

    def add_edge(self, src: str, rel: str, dst: str, props: Mapping[str, Any], single: bool) -> None:
        """Upsert one edge; with `single`, other outgoing edges of the same type are removed."""
        ...

    def remove_edge(self, src: str, rel: str, dst: str) -> bool: ...

    def read_meta(self, name: str) -> dict[str, Any] | None:
        """The identity stamp written for ontology/project `name`, or None if there is none."""
        ...

    def write_meta(self, name: str, value: Mapping[str, Any]) -> None:
        """Store the identity stamp for `name`. Ladybug keeps one stamp per file; Neo4j one per name."""
        ...

    def close(self) -> None: ...
