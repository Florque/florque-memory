# Project mode

A project memory holds two ontologies in one graph and one database:

- a **work** ontology, by default the bundled `workgraph` (projects, goals, initiatives, epics,
  tasks, documents, signals, actors);
- a **domain** ontology you choose, for example `architecture` (systems, components, data entities,
  decisions, findings) or your own.

Work and domain have different owners and change at different speeds. Work edges to domain items
are the link between "what was done" and "what changed in the system".

## 1. Write a manifest

```yaml
# payments.yaml
name: payments-platform        # project name; the database and skill are named after it
work: workgraph                # optional, default: workgraph (bundled name or a file path)
domain: architecture           # required: bundled name or a file path
domain_prefix: dom             # optional, default: dom
db: ./memory/payments.lbdb     # optional: where the graph file lives (default: the data dir)
```

Relative paths (`work`, `domain`, `db`) resolve against the manifest's folder. Unknown keys are rejected.
`work` and `domain` must be different ontologies. The manifest only references the ontologies; it holds no schema.

### Keep a project's data in the project

Put the manifest in the project and set `db` next to it:

```
my-project/
  .memory/
    project.yaml        # name, domain, db: ./graph/payments.lbdb
    graph/payments.lbdb # created on first use
  .claude/skills/       # the project skill is linked here
```

```sh
scripts/install-skill.sh --project my-project/.memory/project.yaml my-project/.claude/skills
```

Then:

- Add `*.lbdb` and `*.lbdb.wal` to the project's `.gitignore`, or commit them if the graph should be shared
  through git (a binary file, so merges are not possible).
- A Ladybug file must not be on a network filesystem or shared between hosts. Keep it on the machine that
  runs the agent.
- Without `db`, the file lives in the data dir and is named after the project, so anyone running the skill
  from any folder shares it.

Precedence for the file: `--db` or `MEMORY_DB_PATH` first, then the manifest `db`, then the data dir.

## 2. Use it

```sh
mem --project payments.yaml describe          # one guide covering both modules
mem --project payments.yaml db-path           # database file and the identity it will be stamped with
export MEMORY_PROJECT=$PWD/payments.yaml      # or set it once for the session
```

Or build a skill for the project:

```sh
memory-engine skill build-project payments.yaml
scripts/install-skill.sh --project payments.yaml claude
```

## 3. How the domain is named in the graph

Every domain class, enum and edge gets the prefix, so names never collide with the work side:

| In the domain ontology | In the project graph |
|---|---|
| class `Component` | `dom_Component` |
| enum `Status` | `dom_Status` |
| edge `part_of` | `dom_part_of` (relationship `DOM_PART_OF`) |

Property names stay as they are, because they live inside their own node table. The work ontology
is never prefixed, so its data is the same in a standalone database and in a project.

A bare name also works when it is unambiguous: `Component` resolves to `dom_Component`. An exact
match always wins, so a work class called `Component` is never shadowed.

## 4. Bridges: linking work to the domain

Bridges are edges from work items to domain items. They are optional, and they are defined by roles
rather than by class names:

- a **domain class** declares the roles it plays: `bridge_role: component`, or several roles
  separated by commas;
- a **work edge** declares the role it targets: `target_role: finding`.

At merge time every role edge is expanded into the domain classes tagged with that role. A role nobody
tags produces no edge, so the work ontology still loads and works on its own.

Allowed roles: `system`, `component`, `finding`, `decision`, `data`.

The bundled ontologies provide these bridges:

| Work edge (from) | Role | Domain classes tagged with it |
|---|---|---|
| `scope` (WorkItem) | system | `System` |
| `targets` (WorkItem) | component | `Component` |
| `addresses` (WorkItem) | finding | `Finding` |
| `changes` (WorkItem) | data | `DataEntity` |
| `implements` (WorkItem) | decision | `Decision` |
| `measures` (Signal) | component | `Component` |

Example, with the architecture domain:

```sh
mem remember Finding ...                       # dom_Finding, must affect a component
mem remember WorkItem -j '{"id":"t-cache", ..., "part_of":"init-reliability",
                           "addresses":["f-cache"], "targets":["checkout-api"]}'
mem context f-cache                            # shows the task as an incoming `addresses` link
```

Bridges have no cycle checks of their own. Cycle and requirement checks stay inside each module.

## 5. What the merged project does not do

- **Containment** (the tree that `mem context` walks) is the work ontology's. A domain edge named
  `part_of` becomes `dom_part_of` and is an ordinary edge, not a containment edge.
- **Cross-module requirements** do not exist: a bridge can never make a class uncreatable.
- **Versions** of each module are not recorded. The identity stamp records names and the prefix only
  (see [database identity](database-identity.md)).

## Related

- [Project merge](../components/project.md): how the merge is implemented.
- [Configuration](../components/config.md): manifest loading and the settings for `--project`.
- [Skill generation](../components/skill.md): the "Work and domain" section of a project skill.
