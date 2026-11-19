# Database identity

Each database file records which ontology or project it was created for. The record is the
**identity stamp**. Opening the database with a different identity is refused before any table is
created or changed, so two ontologies can never silently share one file.

## What is stamped

For a single ontology:

```json
{"schema": 1, "kind": "ontology", "name": "workgraph"}
```

For a project:

```json
{"schema": 1, "kind": "project", "name": "payments-platform",
 "work": "workgraph", "domain": "architecture", "domain_prefix": "dom"}
```

Only these fields are compared. The ontology's content is not, so a grown ontology (new classes or
fields) still migrates as it always did.

## When an open is refused

| Situation | Result |
|---|---|
| New database | stamped on first open |
| Same identity | opens normally |
| Same identity, ontology grew | opens; new tables are added |
| Different domain, or different `domain_prefix` | refused: `ontology_mismatch` |
| Project named like a standalone ontology (for example `workgraph`) | refused: `ontology_mismatch` |
| Database with no stamp (created before this feature) | adopted: the current identity is written |

The refusal is a database-level error, so the CLI exits with code 2 and prints:

```json
{
  "status": "error",
  "error": "ontology_mismatch",
  "message": "This database was created for a different ontology or project. Nothing was written.",
  "issues": [
    "database was created for project `payments` (work `workgraph`, domain `architecture`, prefix `dom`)",
    "it was opened with project `payments` (work `workgraph`, domain `family_tree`, prefix `dom`)",
    "use a different project name or --db, or restore the ontology or manifest it was created with"
  ]
}
```

## How it is stored

- **Ladybug** (one file per ontology or project): a reserved table `_MemoryMeta` holds one row. The
  driver leaves this table out of its catalog, so it never appears in searches, listings, validation
  or context. The id `__meta__` is reserved; the engine refuses it as a node id.
- **Neo4j** (one server, many ontologies): one node per ontology name, labelled `_MemoryMeta` and not
  `Node`, so graph queries do not see it.

## Limits

- A legacy database that was already used with the wrong ontology cannot be detected, because it has
  no stamp. Its first open adopts the current identity.
- `mem db-path` reports the identity the current ontology would stamp. It does not open the file, so
  it cannot show the stored stamp, and it never takes the file lock.

## Related

- [Database drivers](../components/drivers.md): the `read_meta` / `write_meta` methods.
- [Project mode](project-mode.md): the manifest fields that make up a project identity.
