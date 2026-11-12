# memory-engine

Graph memory for coding agents. Agents write what they learn (projects, goals, work items,
documents, signals, actors) into a graph database, and every write is validated against a user-defined
**LinkML** ontology. The engine has no hard-coded labels: swap the ontology and the
CLI, validation and agent guide all follow.

It ships as a JSON-speaking CLI plus an [Agent Skill](skills/graph-memory/SKILL.md),
so any skill-aware agent (Claude Code, Pi, …) can use it without an MCP server.

The default backend is [Ladybug](https://github.com/LadybugDB/ladybug), an embedded Cypher graph
database stored in a single file. It needs no server. Neo4j remains available as an option
(see [Backends](#backends)).

## Quick start

```sh
uv sync
uv run mem describe              # what the ontology allows
uv run mem remember Project -p id=florque -p title=Florque -p summary="Flower shop platform."
uv run mem context florque
```

Install the skill (symlinked, so it tracks this checkout):

```sh
scripts/install-skill.sh claude pi   # ~/.claude/skills and ~/.pi/agent/skills
```

The skill's `scripts/mem` wrapper uses an installed `memory-engine` if present, otherwise
`uv run --project <this repo>`. To install the CLI globally: `uv tool install .`

## Configuration

| Variable | CLI option | Default |
|---|---|---|
| `MEMORY_BACKEND` | `--backend` | `ladybug` (or `neo4j`) |
| `MEMORY_DB_PATH` | `--db PATH` | `memory.lbdb` in the per-user data dir (see below); `:memory:` for a throwaway graph |
| `MEMORY_ONTOLOGY` | `--ontology PATH` | bundled `ontology/workgraph_linkml.yaml` |
| `MEMORY_ACTOR` | | unset; when set, written to `created_by` / `updated_by` |
| `NEO4J_URI` | | `bolt://localhost:7687` |
| `NEO4J_USER` / `NEO4J_PASSWORD` | | `neo4j` / `memory-engine-dev` |
| `NEO4J_DATABASE` | | server default |

CLI options win over environment variables.

## Backends

### Ladybug (default, embedded)

The database is one file plus a transient `.wal`. The CLI opens it for each command and creates
or migrates the tables on open, so you never need `mem init`. Running `mem init` is harmless.

**Where the file lives**

- **CLI / local agents.** The default is the per-user data directory, so one graph is shared by
  every project, agent and session of that OS user. That fits the ontology, since `Project` is a
  node and one graph holds many projects.

  | OS | Default path |
  |---|---|
  | macOS | `~/Library/Application Support/memory-engine/memory.lbdb` |
  | Linux | `$XDG_DATA_HOME/memory-engine/memory.lbdb` (default `~/.local/share/...`) |
  | Windows | `%LOCALAPPDATA%\memory-engine\memory.lbdb` |

  Set `MEMORY_DB_PATH` (or `--db`) for a separate graph, e.g. per client or per experiment.
- **Server / container.** Always set `MEMORY_DB_PATH` explicitly to a path on persistent storage,
  e.g. `/var/lib/memory-engine/memory.lbdb` on a mounted volume. Never point it at the container's
  ephemeral filesystem. Back it up by copying the file while no process has it open.

**Concurrency.** Only one process at a time can open the file read-write. A command that finds
it locked retries for up to 5 seconds, then fails with exit code 2 and `"error": "database_busy"`.
That is enough for several agents issuing short `mem` commands on one machine. Ladybug cannot
share one file across hosts or network filesystems. If many processes or machines must write at
once, either run one long-lived process that owns the file and serves the others, or use Neo4j.

**Schema.** Ladybug is schema-first. Each concrete class becomes a node table with typed columns,
and each edge slot becomes a rel table whose FROM/TO pairs are the class pairs the ontology allows
(`core/schema.py`). When the ontology grows (new classes, slots, edge targets or edge properties),
the change is applied on the next open. Changing a slot's type (e.g. string → integer) cannot be
migrated in place, and the error says so.

### Neo4j (optional server)

```sh
uv sync --extra neo4j            # or: pip install 'memory-engine[neo4j]'
docker compose up -d             # Neo4j 5 on bolt://localhost:7687 (neo4j / memory-engine-dev)
export MEMORY_BACKEND=neo4j
uv run mem init                  # constraints + indexes
```

## Layout

```
ontology/workgraph_linkml.yaml   default ontology (bundled into the wheel)
src/memory_engine/
  core/parser.py      LinkML subset → Ontology model, load-time validation
  core/casting.py     loose input (CLI strings, LLM JSON) → declared types
  core/engine.py      OntologyEngine: validated upserts, links, recall, context, validate
  core/guide.py       ontology → agent-facing markdown / JSON
  core/schema.py      ontology → GraphSchema (node/rel tables and typed columns)
  database/           GraphDriver protocol, open_driver factory,
                      LadybugDriver (embedded, default), Neo4jDynamicDriver (server)
  tools.py            tool registry + handle_agent_call (JSON in, JSON envelope out)
  cli.py              `memory-engine` / `mem`
skills/graph-memory/  SKILL.md, generated references/ontology.md, scripts/mem
```

## How the ontology maps to the graph

- Each concrete class is a node label (Neo4j: plus a shared `:Node` label with a unique `id` constraint;
  Ladybug: a node table with `id` as primary key, and ids are kept unique across tables).
- Slots annotated `edge: true` are relationships, named in UPPER_SNAKE case (`part_of` → `PART_OF`).
  Their targets come from `range` or `slot_usage` `any_of`. Single-valued edges are replaced, not duplicated.
- `edge_properties: "role: Role (required)"` declares typed properties on an edge.
- The containment slot (`part_of`, or one annotated `containment: true`) forms a tree. Cycles are rejected.
- A class's required enum attribute (e.g. `WorkItem.kind`) is its *discriminator*. Annotations on its
  values add constraints: `allowed_parent: "WorkItem:epic,WorkItem:initiative"` and `requires: "serves"`.
- `rules` with `equals_string` preconditions and `required: true` postconditions are enforced.

### Supported LinkML subset

Schema `id/name/title/description/prefixes/default_range/imports (linkml:types only)`;
classes `is_a/abstract/slots/attributes/slot_usage/rules/annotations`;
slots `identifier/required/range/any_of/multivalued/annotations`;
enums with `permissible_values` and annotations;
types `string, integer, float, double, decimal, boolean, date, datetime, uri, uriorcurie, curie, ncname`.
Anything else is rejected at load time rather than silently ignored.

Load-time checks also catch unknown ranges and slots, `is_a` cycles, inconsistent `allowed_parent`
references, and **circular requirements**: classes whose required edges can only be satisfied by
classes that can never be created themselves.

## Writing to memory

```sh
mem remember WorkItem -j '{"title":"Add payment step","summary":"Wizard step to connect payouts.",
                           "kind":"task","part_of":"init-wizard","depends_on":["task-auth"]}'
```

- Upsert by `id`; omitted `id` is generated (`workitem-1a2b3c4d`).
- Edge slots in the payload replace that slot's edges; omitted edge slots are kept. `link` / `unlink` edit one edge.
- New nodes default to `status: proposed`. `null` clears an optional property.
- Rejected writes change nothing. On updates, only *new* violations block the write;
  pre-existing ones are reported by `mem validate`.

Programmatic use:

```python
from memory_engine import OntologyEngine, load_ontology
from memory_engine.database.ladybug_driver import LadybugDriver
from memory_engine.tools import handle_agent_call

ontology = load_ontology("ontology/workgraph_linkml.yaml")
engine = OntologyEngine(ontology, LadybugDriver("memory.lbdb", ontology.identifier))
engine.init()                    # create the tables
handle_agent_call(engine, "get_context", {"id": "init-wizard"})
# Neo4j: Neo4jDynamicDriver("bolt://localhost:7687", ("neo4j", "memory-engine-dev"))
```

`mem tools` lists the tool names and arguments; `mem call <tool> '<json>'` invokes any of them.

## Changing the ontology

Edit or replace the YAML, then regenerate the agent guide the skill ships with:

```sh
uv run mem describe > skills/graph-memory/references/ontology.md
```

A unit test fails if the committed guide is stale.

## Tests

```sh
uv run pytest                     # unit + Ladybug + driver contract; Neo4j tests skip when it is down
docker compose up -d && uv run pytest tests/integration   # adds the Neo4j runs
```

`tests/integration/test_driver_contract.py` runs the same `GraphDriver` contract against the
in-memory fake, Ladybug and Neo4j, so the backends stay interchangeable. Tests never touch your real
graph: `MEMORY_DB_PATH` points at a temp file for every test.
