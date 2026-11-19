# Scripts (`scripts/`)

## `install-skill.sh`

Builds a skill and links it into agent skill folders.

```sh
scripts/install-skill.sh [--reinstall-cli] [ONTOLOGY] [TARGET ...]
scripts/install-skill.sh [--reinstall-cli] --project MANIFEST [TARGET ...]
```

1. **CLI.** If `memory-engine` is not on `PATH`, runs `uv tool install --editable <this repo>`.
   `--reinstall-cli` forces it. The CLI is shared by every skill.
2. **Skill.** Runs `memory-engine skill build` or `skill build-project`. The result goes to
   `<data dir>/skills/<name>-memory`.
3. **Link.** Symlinks the skill into each target. `claude` means `~/.claude/skills`, `pi` means
   `~/.pi/agent/skills`, and any other value is a directory. The default target is `claude`.

Run it again after editing an ontology or manifest; the links stay valid.

## `explore.sh`

Serves a database in Ladybug Explorer for browsing.

```sh
scripts/explore.sh family_tree                     # snapshot at http://localhost:8000
scripts/explore.sh workgraph --port 8001 --live    # read-only view of the live file
```

The official Explorer image lags behind the Ladybug version in use, so the script builds Explorer from
source, pinned to the same Ladybug version. The default serves a snapshot copied while no process holds
the database. `--live` serves the real file read-only; do not write with `mem` while it runs.

## Related

- [Skill generation](skill.md): what the script builds.
- [Project mode](../guides/project-mode.md): the `--project` option in context.
