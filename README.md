# memory-engine

Graph memory for coding agents. Agents write what they learn (projects, goals, work items,
documents, signals, actors) into a graph database, and every write is validated against a user-defined
**LinkML** ontology. The engine has no hard-coded labels: swap the ontology and the
CLI, validation and agent guide all follow.

It ships as a JSON-speaking CLI plus a generator that turns any ontology into an
[Agent Skill](#skills), so any skill-aware agent (Claude Code, Pi, …) can use it without an MCP server.

The default backend is [Ladybug](https://github.com/LadybugDB/ladybug), an embedded Cypher graph
database stored in a single file. It needs no server. Neo4j remains available as an option
(see [Backends](#backends)).

## Quick start

```sh
uv sync
uv run mem describe              # what the bundled workgraph ontology allows
uv run mem remember Project -p id=florque -p title=Florque -p summary="Flower shop platform."
uv run mem context florque
```

Install a skill for the agent:

```sh
scripts/install-skill.sh                                # bundled workgraph ontology -> workgraph-memory, for Claude Code
scripts/install-skill.sh ./my_ontology.yaml claude pi   # your own ontology, for Claude Code and Pi
```

## Skills

`scripts/install-skill.sh [--reinstall-cli] [ONTOLOGY] [TARGET ...]` does three things:

1. **Installs the CLI once, globally.** If `memory-engine` is not on `PATH` it runs
   `uv tool install --editable <this repo>`, so every skill shares one `mem` that tracks this
   checkout. `--reinstall-cli` forces a reinstall.
2. **Generates the skill** with `memory-engine skill build ONTOLOGY` into
   `<data dir>/skills/<ontology name>-memory/`:

   ```
   SKILL.md                    frontmatter + workflow, commands and example writes for this ontology
   references/ontology.md      the full ontology guide (same as `mem describe`)
   references/ontology.yaml    a copy of the ontology, so the skill is self-contained
   scripts/mem                 wrapper that pins MEMORY_ONTOLOGY to that copy, then runs memory-engine
   ```

   Everything is rendered from the YAML by templates; nothing is hand-written or LLM-generated.
   The example writes in SKILL.md are derived from the ontology's required fields and edges and are
   tested to be valid. Set a schema-level `annotations: {skill_description: "..."}` to replace the
   generated frontmatter description.
3. **Symlinks** it into each target: `claude` (`~/.claude/skills`), `pi` (`~/.pi/agent/skills`) or a
   directory (default `claude`).

### Bundled ontologies

`ontologies/` collects ready-to-use ontologies, preferably aligned with open standards so the data
maps to existing tools. Each is usable by name and becomes a `<name>-memory` skill.

| Name | Skill | What it models | Based on |
|---|---|---|---|
| `workgraph` | `workgraph-memory` | Program management: project, objectives / key results, initiatives → epics → tasks, documents, signals, actors | OKRs + agile work breakdown |
| `family_tree` | `family-tree-memory` | Genealogy: trees, people, families, life events, nested historical places, sources with citation quality; claims stay `proposed` until a source confirms them | [GEDCOM 7](https://gedcom.io/specifications/FamilySearchGEDCOMv7.html) (INDI, FAM, EVEN, PLAC, SOUR, PEDI, QUAY) |

To add one, put `<name>_linkml.yaml` in `ontologies/`, cite the standard it follows in `see_also`
and the description, and give every class `use_when` / `not_for` annotations (they steer agents).
`tests/integration/test_bundled_ontologies.py` picks it up automatically: it must load, build a
skill and accept its own generated examples on Ladybug.

`ONTOLOGY` is a `.yaml`/`.yml` path or the name of a bundled ontology in `ontologies/`
(default `workgraph`). After editing the YAML, run the script again: the skill is regenerated in
place and the links stay valid. Install as many ontologies as you like, side by side. Each skill
has its own database (next section), so they never see each other's data.

## Configuration

| Variable | CLI option | Default |
|---|---|---|
| `MEMORY_BACKEND` | `--backend` | `ladybug` (or `neo4j`) |
| `MEMORY_DATA_DIR` | | per-user data dir (see below); holds the databases and generated skills |
| `MEMORY_DB_PATH` | `--db PATH` | `<data dir>/<ontology name>.lbdb`; `:memory:` for a throwaway graph |
| `MEMORY_ONTOLOGY` | `--ontology PATH\|NAME` | bundled `workgraph` (a skill's `scripts/mem` pins its own) |
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

- **One file per ontology.** The default is `<data dir>/<ontology name>.lbdb`, e.g. `workgraph.lbdb`,
  so every skill (= ontology) is isolated, while all projects, agents and sessions using that
  ontology share one graph. Two ontologies must not share a file: their tables would clash.
- **CLI / local agents.** The data dir is the per-user data directory:

  | OS | Data dir |
  |---|---|
  | macOS | `~/Library/Application Support/memory-engine/` |
  | Linux | `$XDG_DATA_HOME/memory-engine/` (default `~/.local/share/memory-engine/`) |
  | Windows | `%LOCALAPPDATA%\memory-engine\` |

  Set `MEMORY_DB_PATH` (or `--db`) for a one-off separate graph, e.g. per client or experiment.
- **Server / container.** Set `MEMORY_DATA_DIR` to persistent storage, e.g. `/var/lib/memory-engine`
  on a mounted volume, so every ontology gets its own file there. Never use the container's
  ephemeral filesystem. Back up by copying the files while no process has them open.

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

### Viewing the graph

```sh
scripts/explore.sh family_tree          # → http://localhost:8000 (Ladybug Explorer)
scripts/explore.sh workgraph --port 8001 --live
docker stop memory-explorer-family-tree
```

The published `ghcr.io/ladybugdb/explorer` image lags behind and cannot open files written by
newer Ladybug versions ("different version… storage version"). `explore.sh` therefore builds
Explorer from its MIT source with `@ladybugdb/core` pinned to the exact `ladybug` version
memory-engine uses (`mem db-path` reports it), cached as `memory-engine/ladybug-explorer:<version>`.
The first build takes a few minutes; after upgrading `ladybug`, the next run builds a new image.

By default it serves a **snapshot**, copied to `<data dir>/explorer/<name>/` while no process has
the database open, because the container cannot see Ladybug's file lock. Re-run to refresh it after
new writes. `--live` serves the real file read-only instead; don't write with `mem` while it runs.

### Neo4j (optional server)

```sh
uv sync --extra neo4j            # or: pip install 'memory-engine[neo4j]'
docker compose up -d             # Neo4j 5 on bolt://localhost:7687 (neo4j / memory-engine-dev)
export MEMORY_BACKEND=neo4j
uv run mem init                  # constraints + indexes
```

## Layout

```
ontologies/           predefined ontologies, bundled into the wheel (workgraph_linkml.yaml → `workgraph`)
scripts/install-skill.sh   build a skill from an ontology and link it into agents
src/memory_engine/
  core/parser.py      LinkML subset → Ontology model, load-time validation
  core/casting.py     loose input (CLI strings, LLM JSON) → declared types
  core/engine.py      OntologyEngine: validated upserts, links, recall, context, validate
  core/guide.py       ontology → agent-facing markdown / JSON
  core/schema.py      ontology → GraphSchema (node/rel tables and typed columns)
  core/skill.py       ontology → Agent Skill (SKILL.md, references, scripts/mem)
  database/           GraphDriver protocol, open_driver factory,
                      LadybugDriver (embedded, default), Neo4jDynamicDriver (server)
  tools.py            tool registry + handle_agent_call (JSON in, JSON envelope out)
  config.py           settings, data dir, bundled ontology lookup
  cli.py              `memory-engine` / `mem`
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

Schema `id/name/title/description/prefixes/default_range/imports (linkml:types only)/annotations`;
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
- New nodes default to `status: proposed` (when the ontology has that status). `null` clears an optional property.
- Rejected writes change nothing. On updates, only *new* violations block the write;
  pre-existing ones are reported by `mem validate`.

Programmatic use:

```python
from memory_engine import OntologyEngine, load_ontology
from memory_engine.database.ladybug_driver import LadybugDriver
from memory_engine.tools import handle_agent_call

ontology = load_ontology("ontologies/workgraph_linkml.yaml")
engine = OntologyEngine(ontology, LadybugDriver("memory.lbdb", ontology.identifier))
engine.init()                    # create the tables
handle_agent_call(engine, "get_context", {"id": "init-wizard"})
# Neo4j: Neo4jDynamicDriver("bolt://localhost:7687", ("neo4j", "memory-engine-dev"))
```

`mem tools` lists the tool names and arguments; `mem call <tool> '<json>'` invokes any of them.

## Changing the ontology

Edit the YAML (or write a new one), then rebuild its skill:

```sh
scripts/install-skill.sh path/to/ontology.yaml     # or: memory-engine skill build path/to/ontology.yaml
```

New classes, slots and edge targets are added to the existing database on the next command.
To add a predefined ontology to the repo, drop it into `ontologies/`; it is then usable by name
(`--ontology NAME`, `install-skill.sh NAME`).

## Tests

```sh
uv run pytest                     # unit + Ladybug + driver contract; Neo4j tests skip when it is down
docker compose up -d && uv run pytest tests/integration   # adds the Neo4j runs
```

`tests/integration/test_driver_contract.py` runs the same `GraphDriver` contract against the
in-memory fake, Ladybug and Neo4j, so the backends stay interchangeable. Tests never touch your real
graph or skills: `MEMORY_DATA_DIR` points at a temp dir for every test.
