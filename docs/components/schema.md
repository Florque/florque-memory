# Schema (`core/schema.py`)

Turns an `Ontology` into the tables a schema-first backend needs. Ladybug uses it directly. Neo4j is
schemaless, so it only uses the labels.

## Output

`schema_for(ontology)` returns a `GraphSchema`:

- **Node tables**: one per concrete class. Columns are the identifier, every property slot and the
  system columns `created_at`, `updated_at`, `created_by`, `updated_by`.
- **Relationship tables**: one per edge type (the upper-case slot name). Each lists every
  (source, target) class pair the ontology can allow, plus the edge properties and the timestamps.

## Rules

- Column types come from the range: enums and text types are strings; a multi-range slot falls back to
  string.
- A multivalued property is a list column.
- An edge with no resolved targets (a bridge with no domain class tagged for its role) produces no
  table. Nothing has to be cleaned up.
- Edges with the same name, in any classes, share one table, with their pairs unioned. This is how a
  project avoids duplicate relationship tables.
- Target labels include every concrete subclass of a target class, across all discriminator values.

## Migrations

The driver applies the schema when the database is opened. New tables, columns and pairs are added. A
column whose type changed is refused with a message: it needs a rebuild. See
[drivers](drivers.md).

## Related

- [Ontology parser](parser.md): the model this module reads.
- [Drivers](drivers.md): where the schema is applied.
