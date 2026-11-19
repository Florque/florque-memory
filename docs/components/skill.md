# Skill generation (`core/skill.py`)

Turns an ontology, or a project, into an Agent Skill directory. Everything in the skill is rendered
from the YAML by templates; nothing is written by hand or by a language model.

## Output

A single-ontology skill, named `<ontology name>-memory`:

```
SKILL.md                   frontmatter, workflow, commands, payload rules, examples
references/ontology.md     the full ontology guide
references/ontology.yaml   a copy of the ontology
scripts/mem                wrapper that pins MEMORY_ONTOLOGY, then runs memory-engine
.memory-engine-skill       marker: identifies a directory this tool may replace
```

A project skill is named `<project name>-memory` and also holds:

```
references/project.yaml    the manifest, with relative paths
references/work.yaml       copy of the work ontology
references/domain.yaml     copy of the domain ontology
```

The wrapper sets `MEMORY_PROJECT` (and unsets `MEMORY_ONTOLOGY`) instead of `MEMORY_ONTOLOGY`. The
copies make the skill self-contained: it works when the original files move. When the manifest sets
`db`, the copied `project.yaml` holds the absolute database path, resolved when the skill was built, so
the skill finds the same file from any working directory.

## Functions

| Function | Purpose |
|---|---|
| `build_skill(path, out)` | Single ontology |
| `build_project_skill(manifest, out)` | Project: loads both ontologies, copies them and the manifest |
| `render_skill_md(ontology)` | SKILL.md text; adds a "Work and domain" section for projects |
| `example_payloads(ontology)` | Valid writes built from required fields and edges; tests check they pass |
| `render_wrapper(ontology, home, project)` | The `scripts/mem` shell script |

## The "Work and domain" section

Present only for projects. It lists the work classes (plain names), the domain classes (the prefix),
and every bridge with the role it targets and the domain classes it can reach. A bridge whose role has no
tagged class says so, so an agent does not try to use it.

## Safety

- A directory is replaced only if it holds the marker file. Otherwise the build refuses.
- The build writes to a temporary directory and swaps it in. A failed build leaves no partial skill.

## Examples

`example_payloads` orders writes so each one only points at nodes that already exist. The tests run them
against Ladybug, so a skill's examples are always valid for its ontology. The richest example's
`mem context` is shown last.

## Related

- [Project mode](../guides/project-mode.md): what a project skill contains.
- [Scripts](scripts.md): `install-skill.sh` builds the skill and links it into agent folders.
