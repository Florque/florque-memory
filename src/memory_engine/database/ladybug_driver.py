"""Ladybug (embedded, Kuzu lineage) implementation of GraphDriver.

Ladybug is schema-first: one node table per ontology class and one rel table per edge type,
each with typed columns (see core/schema.py). `ensure_schema` creates them and adds new
columns / FROM-TO pairs when the ontology grows. The live catalog is read back from the
database, so queries only reference columns that exist.

Labels, relationship types and property keys are interpolated only through `ident`;
values are always query parameters (or a literal NULL).

One process at a time may open a database file read-write. Opening a locked file is
retried for `lock_timeout` seconds, then fails with DatabaseBusyError.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import ladybug as lb

from ..core.errors import (
    DatabaseBusyError,
    DatabaseError,
    DatabaseUnavailableError,
    NotFoundError,
    ValidationError,
)
from .base import Column, Edge, EdgeTargets, GraphSchema, Node, ident

MEMORY = ":memory:"

_TYPES = {
    "string": "STRING",
    "integer": "INT64",
    "float": "DOUBLE",
    "boolean": "BOOL",
    "date": "DATE",
    "datetime": "TIMESTAMP",
}
_TYPE_ALIASES = {"BOOLEAN": "BOOL", "INT": "INT64", "STRING": "STRING"}


def _ddl_type(column: Column) -> str:
    base = _TYPES[column.type]
    return f"{base}[]" if column.list else base


def _norm_type(raw: str) -> str:
    raw = raw.strip().upper()
    suffix = "[]" if raw.endswith("[]") else ""
    base = raw[:-2] if suffix else raw
    return _TYPE_ALIASES.get(base, base) + suffix


def _to_db(value: Any) -> Any:
    """Ladybug TIMESTAMP has no zone: store UTC."""
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    if isinstance(value, (list, tuple)):
        return [_to_db(v) for v in value]
    return value


def _from_db(value: Any) -> Any:
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    if isinstance(value, list):
        return [_from_db(v) for v in value]
    return value


def _props(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Drop internal fields (`_ID`, `_LABEL`, ...) and NULL columns: the sparse shape Neo4j returns."""
    return {k: _from_db(v) for k, v in raw.items() if not k.startswith("_") and v is not None}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _is_lock_error(exc: Exception) -> bool:
    return "lock" in str(exc).lower()


class LadybugDriver:
    def __init__(self, path: str | Path = MEMORY, id_key: str = "id", lock_timeout: float = 5.0):
        self.path = str(path) if str(path) == MEMORY else str(Path(path).expanduser())
        self.id_key = id_key
        self._id = ident(id_key)
        self._lock = threading.RLock()
        self._db, self._conn = self._open(lock_timeout)
        self._columns: dict[str, dict[str, str]] = {}  # node label -> column -> type
        self._rels: dict[str, set[tuple[str, str]]] = {}  # rel type -> (src label, dst label)
        self._rel_columns: dict[str, dict[str, str]] = {}
        self._load_catalog()

    # ---------------------------------------------------------------- connection

    def _open(self, lock_timeout: float) -> tuple[Any, Any]:
        if self.path != MEMORY:
            try:
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise DatabaseUnavailableError(f"Cannot create the directory for {self.path}: {exc}") from exc
        deadline = time.monotonic() + max(0.0, lock_timeout)
        delay = 0.05
        while True:
            try:
                db = lb.Database(self.path)
                return db, lb.Connection(db)
            except RuntimeError as exc:
                if not _is_lock_error(exc):
                    raise DatabaseUnavailableError(f"Cannot open Ladybug database at {self.path}: {exc}") from exc
                if time.monotonic() >= deadline:
                    raise DatabaseBusyError(
                        f"Ladybug database {self.path} is locked by another process. Only one process can "
                        "open it read-write: retry shortly, stop the other process, or point MEMORY_DB_PATH "
                        "elsewhere. For many concurrent writers use the Neo4j backend."
                    ) from exc
                time.sleep(delay)
                delay = min(delay * 2, 0.5)

    def verify(self) -> None:
        self._rows("RETURN 1")

    def close(self) -> None:
        with self._lock:
            for handle in (self._conn, self._db):
                try:
                    handle.close()
                except Exception:  # noqa: BLE001 - closing twice or after a failure is fine
                    pass

    def _rows(self, query: str, params: Mapping[str, Any] | None = None) -> list[list[Any]]:
        with self._lock:
            try:
                result = self._conn.execute(query, {k: _to_db(v) for k, v in (params or {}).items()})
                return result.get_all()
            except RuntimeError as exc:
                raise DatabaseError(f"Ladybug query failed: {exc}") from exc

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            self._rows("BEGIN TRANSACTION")
            try:
                yield
            except BaseException:
                try:
                    self._rows("ROLLBACK")
                except DatabaseError:
                    pass  # a failed statement may already have aborted the transaction
                raise
            self._rows("COMMIT")

    # ---------------------------------------------------------------- schema

    def _load_catalog(self) -> None:
        self._columns, self._rels, self._rel_columns = {}, {}, {}
        for name, kind in self._rows("CALL show_tables() RETURN name, type"):
            info = {
                str(col): _norm_type(str(typ))
                for col, typ in self._rows(f"CALL table_info('{name}') RETURN name, type")
            }
            if kind == "NODE":
                self._columns[name] = info
            elif kind == "REL":
                self._rel_columns[name] = info
                self._rels[name] = {
                    (str(row[0]), str(row[1])) for row in self._rows(f"CALL show_connection('{name}') RETURN *")
                }

    def ensure_schema(self, schema: GraphSchema) -> None:
        if schema.id_key != self.id_key:
            raise ValueError(f"Schema identifier `{schema.id_key}` does not match driver id_key `{self.id_key}`")
        statements: list[str] = []
        for label, columns in schema.node_tables.items():
            existing = self._columns.get(label)
            if existing is None:
                cols = [f"{self._id} STRING PRIMARY KEY"] + [
                    f"{ident(name)} {_ddl_type(col)}" for name, col in columns.items() if name != self.id_key
                ]
                statements.append(f"CREATE NODE TABLE IF NOT EXISTS {ident(label)}({', '.join(cols)})")
            else:
                statements += self._add_columns(label, existing, columns)
        for rel, table in schema.rel_tables.items():
            existing_pairs = self._rels.get(rel)
            if existing_pairs is None:
                parts = [f"FROM {ident(a)} TO {ident(b)}" for a, b in table.pairs]
                parts += [f"{ident(name)} {_ddl_type(col)}" for name, col in table.props.items()]
                statements.append(f"CREATE REL TABLE IF NOT EXISTS {ident(rel)}({', '.join(parts)})")
            else:
                statements += [
                    f"ALTER TABLE {ident(rel)} ADD FROM {ident(a)} TO {ident(b)}"
                    for a, b in table.pairs
                    if (a, b) not in existing_pairs
                ]
                statements += self._add_columns(rel, self._rel_columns[rel], table.props)
        if not statements:
            return
        try:
            for statement in statements:
                self._rows(statement)
        finally:
            self._load_catalog()

    def _add_columns(self, table: str, existing: Mapping[str, str], columns: Mapping[str, Column]) -> list[str]:
        statements = []
        for name, col in columns.items():
            if name not in existing:
                statements.append(f"ALTER TABLE {ident(table)} ADD {ident(name)} {_ddl_type(col)}")
            elif existing[name] != _ddl_type(col):
                raise DatabaseError(
                    f"`{table}.{name}` is stored as {existing[name]} but the ontology now needs "
                    f"{_ddl_type(col)}. Changing a column type needs a rebuild: export, delete "
                    f"{self.path} and re-import."
                )
        return statements

    # ---------------------------------------------------------------- reads

    def _node(self, raw: Mapping[str, Any]) -> Node:
        props = _props(raw)
        return Node(str(props.get(self.id_key)), str(raw["_LABEL"]), props)

    def get_nodes(self, ids: Iterable[str]) -> dict[str, Node]:
        ids = list(ids)
        if not ids or not self._columns:
            return {}
        rows = self._rows(f"MATCH (n) WHERE n.{self._id} IN $ids RETURN n", {"ids": ids})
        nodes = (self._node(row[0]) for row in rows)
        return {n.id: n for n in nodes}

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
        candidates = [label for label in (self._columns if labels is None else labels) if label in self._columns]
        # a node can only have an outgoing `rel` if its table is a FROM side of that rel table
        for rel in edge_filters or {}:
            sources = {src for src, _ in self._rels.get(rel, ())}
            candidates = [label for label in candidates if label in sources]
        if not candidates:
            return []
        present = set().union(*(self._columns[label] for label in candidates))

        params: dict[str, Any] = {}
        where: list[str] = []
        for i, (key, value) in enumerate((filters or {}).items()):
            if key not in present:
                return []  # no candidate table stores this property
            if value is None:
                where.append(f"n.{ident(key)} IS NULL")
                continue
            params[f"f{i}"] = value
            where.append(f"n.{ident(key)} = $f{i}")
        for i, (key, value) in enumerate((exclude or {}).items()):
            if key in present and value is not None:
                params[f"x{i}"] = value
                where.append(f"coalesce(n.{ident(key)} <> $x{i}, true)")
        for i, (rel, dst) in enumerate((edge_filters or {}).items()):
            params[f"e{i}"] = dst
            where.append(f"EXISTS {{ MATCH (n)-[:{ident(rel)}]->(m) WHERE m.{self._id} = $e{i} }}")
        if text:
            keys = [k for k in text_keys if k in present]
            if not keys:
                return []
            params["text"] = text.lower()
            where.append("(" + " OR ".join(
                f"coalesce(lower(cast(n.{ident(k)}, 'STRING')) CONTAINS $text, false)" for k in keys
            ) + ")")

        query = f"MATCH (n:{':'.join(ident(label) for label in candidates)})"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " RETURN n ORDER BY coalesce(n.updated_at, n.created_at) DESC"
        if limit is not None:
            query += f" LIMIT {max(0, int(limit))}"
        return [self._node(row[0]) for row in self._rows(query, params)]

    def edges(
        self,
        from_ids: Iterable[str] | None = None,
        to_ids: Iterable[str] | None = None,
        rel: str | None = None,
    ) -> list[Edge]:
        if not self._rels or (rel is not None and rel not in self._rels):
            return []
        params: dict[str, Any] = {}
        where = []
        if from_ids is not None:
            params["from_ids"] = list(from_ids)
            where.append(f"a.{self._id} IN $from_ids")
        if to_ids is not None:
            params["to_ids"] = list(to_ids)
            where.append(f"b.{self._id} IN $to_ids")
        if any(not v for v in params.values()):
            return []
        rel_pattern = f":{ident(rel)}" if rel else ""
        query = f"MATCH (a)-[r{rel_pattern}]->(b)"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += f" RETURN a.{self._id}, label(r), b.{self._id}, r"
        return [Edge(str(src), str(r), str(dst), _props(props)) for src, r, dst, props in self._rows(query, params)]

    # ---------------------------------------------------------------- writes

    def _check_columns(self, table: str, columns: Mapping[str, str], keys: Iterable[str]) -> None:
        missing = sorted(k for k in keys if k not in columns)
        if missing:
            raise DatabaseError(
                f"Table `{table}` has no column(s) {', '.join(missing)}; the database schema is older "
                "than the ontology. Run `mem init`."
            )

    def _set_clause(self, var: str, props: Mapping[str, Any], prefix: str, params: dict[str, Any]) -> list[str]:
        clauses = []
        for i, (key, value) in enumerate(props.items()):
            if value is None:
                clauses.append(f"{var}.{ident(key)} = NULL")
            else:
                params[f"{prefix}{i}"] = value
                clauses.append(f"{var}.{ident(key)} = ${prefix}{i}")
        return clauses

    def _label_of(self, node_id: str) -> str | None:
        node = self.get_nodes([node_id]).get(node_id)
        return node.cls if node else None

    def _merge_edge(self, src: str, src_label: str, rel: str, dst: str, props: Mapping[str, Any], now: datetime) -> None:
        dst_label = self._label_of(dst)
        if dst_label is None:
            raise NotFoundError(f"Cannot link `{src}` -[{rel}]-> `{dst}`: a node is missing")
        if (src_label, dst_label) not in self._rels.get(rel, set()):
            raise DatabaseError(
                f"The database has no {rel} relationship from {src_label} to {dst_label}. "
                "Run `mem init` after changing the ontology."
            )
        self._check_columns(rel, self._rel_columns[rel], props)
        params: dict[str, Any] = {"src": src, "dst": dst, "now": now}
        sets = self._set_clause("r", props, "p", params) + ["r.updated_at = $now"]
        self._rows(
            f"MATCH (a:{ident(src_label)} {{{self._id}: $src}}), (b:{ident(dst_label)} {{{self._id}: $dst}}) "
            f"MERGE (a)-[r:{ident(rel)}]->(b) ON CREATE SET r.created_at = $now SET {', '.join(sets)}",
            params,
        )

    def _drop_other_edges(self, src: str, src_label: str, rel: str, keep: list[str]) -> None:
        if not any(a == src_label for a, _ in self._rels.get(rel, ())):
            return
        params: dict[str, Any] = {"src": src}
        query = f"MATCH (a:{ident(src_label)} {{{self._id}: $src}})-[r:{ident(rel)}]->(b)"
        if keep:
            params["keep"] = keep
            query += f" WHERE NOT (b.{self._id} IN $keep)"
        self._rows(query + " DELETE r", params)

    def write_node(
        self,
        node_id: str,
        label: str,
        props: Mapping[str, Any],
        create_props: Mapping[str, Any],
        replace_edges: Mapping[str, EdgeTargets],
    ) -> None:
        columns = self._columns.get(label)
        if columns is None:
            raise DatabaseError(f"The database has no `{label}` table. Run `mem init`.")
        self._check_columns(label, columns, [*props, *create_props])
        now = _now()
        with self._transaction():
            current = self._label_of(node_id)
            if current is not None and current != label:
                raise ValidationError(f"`{node_id}` is a {current}, not a {label}")
            params: dict[str, Any] = {"id": node_id, "now": now}
            on_create = self._set_clause("n", create_props, "c", params) + ["n.created_at = $now"]
            sets = self._set_clause("n", props, "p", params) + ["n.updated_at = $now"]
            self._rows(
                f"MERGE (n:{ident(label)} {{{self._id}: $id}}) "
                f"ON CREATE SET {', '.join(on_create)} SET {', '.join(sets)}",
                params,
            )
            for rel, targets in replace_edges.items():
                self._drop_other_edges(node_id, label, rel, [dst for dst, _ in targets])
                for dst, edge_props in targets:
                    self._merge_edge(node_id, label, rel, dst, edge_props, now)

    def add_edge(self, src: str, rel: str, dst: str, props: Mapping[str, Any], single: bool) -> None:
        with self._transaction():
            src_label = self._label_of(src)
            if src_label is None:
                raise NotFoundError(f"Cannot link `{src}` -[{rel}]-> `{dst}`: a node is missing")
            if single:
                self._drop_other_edges(src, src_label, rel, [dst])
            self._merge_edge(src, src_label, rel, dst, props, _now())

    def remove_edge(self, src: str, rel: str, dst: str) -> bool:
        if rel not in self._rels:
            return False
        with self._transaction():
            src_label = self._label_of(src)
            if src_label is None or not any(a == src_label for a, _ in self._rels[rel]):
                return False
            rows = self._rows(
                f"MATCH (a:{ident(src_label)} {{{self._id}: $src}})-[r:{ident(rel)}]->(b) "
                f"WHERE b.{self._id} = $dst DELETE r RETURN count(*)",
                {"src": src, "dst": dst},
            )
            return bool(rows and rows[0][0])
