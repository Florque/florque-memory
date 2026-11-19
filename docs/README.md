# florque-memory documentation

florque-memory stores what agents learn as a typed graph. An ontology (LinkML) defines what may be
stored; the engine turns it into an Agent Skill, validates every write, and keeps the data in an
embedded graph database.

Start with the [README](../README.md) for installation and daily use. These pages describe how the
main components work.

## Guides

- [Project mode](guides/project-mode.md): one memory that combines a work ontology and a domain ontology.
- [Database identity](guides/database-identity.md): how a database is bound to the ontology it was created for.

## Components

| Component | Code | What it does |
|---|---|---|
| [Configuration](components/config.md) | `config.py` | Settings, data directory, ontology and manifest lookup |
| [Ontology parser](components/parser.md) | `core/parser.py` | LinkML subset to the internal model, with load-time checks |
| [Project merge](components/project.md) | `core/project.py` | Merges a work and a domain ontology into one graph |
| [Engine](components/engine.md) | `core/engine.py` | Validated writes, links, reads, context and graph checks |
| [Schema](components/schema.md) | `core/schema.py` | Ontology to node and relationship tables |
| [Drivers](components/drivers.md) | `database/` | Storage interface, Ladybug and Neo4j implementations |
| [Skill generation](components/skill.md) | `core/skill.py` | SKILL.md, references and the pinned `scripts/mem` wrapper |
| [Guide and casting](components/guide-and-casting.md) | `core/guide.py`, `core/casting.py` | Agent-facing ontology guide; loose input to typed values |
| [Agent tools](components/tools.md) | `tools.py` | JSON tool registry for agent frameworks |
| [CLI](components/cli.md) | `cli.py` | The `mem` / `memory-engine` command line |
| [Scripts](components/scripts.md) | `scripts/` | Skill installation and the Ladybug Explorer viewer |
| [Errors](components/errors.md) | `core/errors.py` | Error types, codes and exit codes |

## How the pieces fit

```
ontology.yaml ──► parser ──► Ontology ──► schema ──► driver (Ladybug | Neo4j)
                               │                         ▲
manifest + work + domain ─► project merge ──► Ontology   │
                               │                         │
                               ├──► skill (SKILL.md, references, scripts/mem)
                               │
                               └──► engine ──► tools / CLI ──► agent
                                      ▲
                       identity stamp checked on open (driver)
```

A request from an agent goes through the CLI or the tool registry, then the engine validates it
against the ontology, and only then does the driver write. A rejected request changes nothing.
