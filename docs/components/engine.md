# Engine (`core/engine.py`)

`OntologyEngine` checks every agent request against the ontology, then applies it through a
[driver](drivers.md). It never writes anything it has not validated, so a rejected request changes
nothing.

## Operations

| Method | Purpose |
|---|---|
| `init()` | Create tables or constraints (idempotent); reports labels and identifier |
| `remember(class, payload)` | Create or update a node, with its edges, upserting by `id` |
| `link(src, slot, dst, props)` | Add one edge; a single-valued slot replaces the old edge |
| `unlink(src, slot, dst)` | Remove one edge; refused if the node requires it |
| `recall(class, filters, limit)` | Exact-match listing; an edge slot filters by target id |
| `search(text, class)` | Substring search over the identifier and text fields |
| `get(id, depth)` | A node and its neighbours up to depth 3 |
| `context(id)` | Working context: the containment chain up to the root, children and links |
| `validate()` | Checks the whole graph against the ontology |

## Write rules

- **Upsert by id.** An omitted id is generated (`<class>-<8 hex>`). An existing node of another class
  is an error.
- **Edges in a payload.** An edge slot present in the payload replaces that slot's outgoing edges. Slots
  left out are kept. Targets are ids, lists of ids, or `{"id": ..., <edge property>: ...}` objects.
- **Defaults.** New nodes start as the ontology's default status when it has one (`proposed` for
  workgraph), so people accept agent proposals.
- **System fields.** `created_*` and `updated_*` are set by the engine. A payload may not set them.
- **Reserved id.** `__meta__` is refused; see [database identity](../guides/database-identity.md).
- **Only new violations block a write.** On update, violations that already existed are reported by
  `validate()` instead of blocking the write.
- **Required edges and rules.** Checked before the write, using the static requirements of the class and
  its discriminator value.

## Containment

When the ontology has a containment slot (`part_of`), the engine:

- rejects an edge that would create a cycle (`would create a containment cycle`);
- uses it for `context` (ancestors up to the root) and for the root check.

Domain edges in a project are not containment, so they are not checked this way.

## Reads

`recall` and `search` hide archived nodes unless `include_archived` is passed, when the ontology has an
archive state. Reads never mutate.

## Related

- [Schema](schema.md): the tables the engine writes to.
- [Guide and casting](guide-and-casting.md): how loose input becomes typed values.
- [Agent tools](tools.md): the same operations as named JSON tools.
