# Database drivers (`database/`)

The engine depends only on the `GraphDriver` protocol (`database/base.py`). Three implementations
exist: Ladybug (embedded, default), Neo4j (server), and an in-memory fake used by unit tests.

## The protocol

| Group | Methods |
|---|---|
| Schema | `ensure_schema(schema)` |
| Reads | `get_nodes(ids)`, `find_nodes(labels, filters, edge_filters, text, text_keys, exclude, limit)`, `edges(from_ids, to_ids, rel)` |
| Writes | `write_node(id, label, props, create_props, replace_edges)`, `add_edge(...)`, `remove_edge(...)` |
| Identity | `read_meta(name)`, `write_meta(name, value)` |
| Lifecycle | `close()` |

`ident(name)` in `base.py` is the only way labels, relationship types and property keys reach a query.
It checks that the name is a plain identifier and back-quotes it. Values are always query parameters.

## Opening a driver

`database/__init__.py: open_driver(settings, ontology)`:

1. Build the identity (`core/identity.py`).
2. Ladybug: open the file (retrying a lock for up to 5 seconds, then `database_busy`). Neo4j: connect
   and verify.
3. Read the stored identity; refuse a mismatch with `ontology_mismatch`. Nothing is created or changed
   on refusal.
4. Ladybug: apply the schema. Neo4j: schema is not applied here; `mem init` creates its constraints.
5. Write the identity if it differs from the stored one (first open, or adoption).

## Ladybug (`ladybug_driver.py`)

- One node table per class and one relationship table per edge type, with typed columns
  (see [schema](schema.md)).
- `ensure_schema` adds missing tables, columns and relationship pairs. A type change is refused.
- The catalogue is read back on open, so queries only use columns that exist.
- The identity lives in the reserved table `_MemoryMeta`, which the catalogue leaves out.
- Only one process can open a file read-write. The engine is designed for one writer per file.

## Neo4j (`neo4j_driver.py`)

- Every node has the `Node` label plus its class label. Constraints and an index on the identifier are
  created by `mem init`.
- Cypher labels and relationship types are interpolated only after `ident` checks them. Property keys
  are never interpolated.
- Native driver errors are translated into the `DatabaseError` family.
- The identity is one `_MemoryMeta` node per ontology name, so one server can host several ontologies.
- Install with `pip install 'memory-engine[neo4j]'`.

## Fake (`tests/fakes.py: InMemoryDriver`)

Same semantics as the other two, in memory. Used by unit tests of the engine, parser and skill
generation.

## Contract tests

`tests/integration/test_driver_contract.py` runs the same tests against the fake, Ladybug and Neo4j. Neo4j
is skipped when no server is reachable. The identity round-trip is part of the contract.

## Related

- [Database identity](../guides/database-identity.md): what the stamp means.
- [Configuration](config.md): how the backend and file path are chosen.
