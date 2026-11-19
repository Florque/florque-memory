# CLI (`cli.py`)

The `memory-engine` command, also installed as `mem`. Every command prints one JSON object.

## Global options

These apply to every command and must come before it.

| Option | Environment variable | Meaning |
|---|---|---|
| `--ontology PATH\|NAME` | `MEMORY_ONTOLOGY` | Single ontology |
| `--project PATH` | `MEMORY_PROJECT` | Project manifest (replaces `--ontology`) |
| `--db PATH` | `MEMORY_DB_PATH` | Database file, or `:memory:` |
| `--backend ladybug\|neo4j` | `MEMORY_BACKEND` | Storage backend |

## Commands

| Command | Purpose |
|---|---|
| `init` | Create tables or constraints (idempotent) |
| `describe [--class C] [--format md\|json]` | The ontology guide |
| `remember CLASS -j JSON` or `-p key=value` | Create or update a node and its edges |
| `link FROM SLOT TO [-p key=value]` | Add one edge |
| `unlink FROM SLOT TO` | Remove one edge |
| `recall CLASS [-w slot=value] [--limit N]` | Exact-match listing |
| `search TEXT [--class C]` | Substring search |
| `get ID [--depth 1-3]` | A node and its neighbours |
| `context ID` | Working context: containment chain, children, links |
| `validate` | Check the whole graph |
| `call TOOL JSON` | Invoke an agent tool |
| `tools` | List agent tools |
| `db-path` | Database file, Ladybug version and the identity this ontology stamps |
| `skill build ONTOLOGY [--out DIR]` | Generate a single-ontology skill |
| `skill build-project MANIFEST [--out DIR]` | Generate a project skill |

## Exit codes

| Code | Meaning | Examples |
|---|---|---|
| 0 | Success | |
| 1 | Request rejected; fix it using `issues` | validation error, unknown class, not found, invalid ontology |
| 2 | Database unusable | `database_busy`, `database_unavailable`, `ontology_mismatch` |

## Behaviour

- Ontology and manifest errors are printed as the error envelope before any command runs.
- `db-path` does not open the database, so it never waits for the file lock.
- `describe` reads the ontology only, so it works without a database.

## Related

- [Configuration](config.md): how the options and variables are resolved.
- [Errors](errors.md): the codes in the envelope.
- [Agent tools](tools.md): the same operations as named tools.
