# Configuration (`config.py`)

Reads settings from the environment and resolves where ontologies, databases and skills live.
Explicit arguments (CLI options) win over environment variables, which win over defaults.

## Settings

`Settings.from_env(ontology_path, db_path, backend, project)` returns an immutable `Settings`:

| Field | Source | Meaning |
|---|---|---|
| `backend` | `--backend` / `MEMORY_BACKEND` | `ladybug` (default) or `neo4j` |
| `db_path` | `--db` / `MEMORY_DB_PATH` | Explicit database file, or `:memory:`; `None` = default |
| `ontology_path` | `--ontology` / `MEMORY_ONTOLOGY` | Single-ontology mode; `None` in project mode |
| `project_path` | `--project` / `MEMORY_PROJECT` | Project manifest; set means project mode |
| `actor` | `MEMORY_ACTOR` | Written to `created_by` / `updated_by` |
| `neo4j_*` | `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` | Neo4j connection |

Passing both `--ontology` and `--project` is an error. In project mode the environment's
`MEMORY_ONTOLOGY` is ignored.

## Data directory

`data_dir()` is `$MEMORY_DATA_DIR` if set; otherwise the per-user data directory (macOS
`~/Library/Application Support/memory-engine`, Linux `$XDG_DATA_HOME/memory-engine`, Windows
`%LOCALAPPDATA%\memory-engine`). It holds the databases and the generated skills.

- `default_db_path(name)` is `<data dir>/<slug(name)>.lbdb`. For a project the name is the project name.
- `slug(name)` makes a file- and skill-safe name: `Work Graph` becomes `work-graph`.

## Database file precedence

1. `--db` / `MEMORY_DB_PATH`
2. the manifest's `db`
3. `default_db_path(name)`, the data dir file named after the project

`Settings.from_env` applies this rule. `Settings.db_path` holds the result, which `open_driver` and
`mem db-path` use directly.

## Ontology lookup

- `bundled_ontologies()` maps short names to files in `ontologies/` (`workgraph_linkml.yaml` becomes
  `workgraph`). Both the wheel and a source checkout are supported.
- `resolve_ontology(ref)` accepts a file path or a bundled name, and raises `OntologyError` listing the
  bundled names otherwise.

## Project manifests

`load_manifest(path)` returns a `ProjectManifest(name, work, domain, domain_prefix, source)`.

Rules:

- `name` is required and non-empty.
- `domain` is required.
- `work` defaults to `workgraph`; `domain_prefix` defaults to `dom`.
- `db` is an optional file path, resolved against the manifest's folder, or `:memory:`. It must be quoted
  in YAML when it is `:memory:`. Without it the database is `<data dir>/<name>.lbdb`.
- Unknown keys are rejected; `work` and `domain` must differ.
- A relative `work` or `domain` path is looked up next to the manifest first, then as a bundled name.

The manifest only references ontologies. Merging happens in [project merge](project.md).

## Related

- [Project mode](../guides/project-mode.md) for the manifest in use.
- [CLI](cli.md) for the options that set these values.
