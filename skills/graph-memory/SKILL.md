---
name: graph-memory
description: Persistent project memory in a graph database governed by an ontology (projects, goals and key results, initiatives/epics/tasks/bugs, documents, signals, actors). Use it to recall why work exists, who owns it, what it depends on and which specs or metrics relate to it before starting a task, and to record new work items, decisions documents, findings or metrics so they persist across sessions and agents.
---

# Graph memory

A shared, validated memory graph. Every write is checked against the ontology in
`references/ontology.md`; invalid writes are rejected with the reasons, and nothing is saved.

Run commands through the wrapper in this skill's directory:

```sh
<skill-dir>/scripts/mem <command> ...
```

Every command prints one JSON object: `{"status": "success", ...}` or
`{"status": "error", "error": "<code>", "message": ..., "issues": [...]}`.
Exit code 1 means the request was rejected (fix it using `issues`);
exit code 2 means the database is unusable. With `"error": "database_busy"` another process holds
the embedded database file: wait a few seconds and retry. For `database_unavailable` with the Neo4j
backend, tell the user to run `docker compose up -d` in the memory-engine repo; otherwise report the message.

## Workflow

1. **Know the ontology first.** Read `references/ontology.md` once per session
   (or `mem describe --class WorkItem` for one class). Use only the classes, slots and
   values it lists. Pick the class by its *Use when* / *Not for* notes.
2. **Look before you write.** `mem search "<words>"` or `mem recall <Class> -w key=value`
   to find existing nodes. Update them by `id` instead of creating duplicates.
3. **Load context before working on an item.** `mem context <id>` returns the containment
   chain to the root, children, goals served, owner, dependencies and the documents and
   signals about it or its ancestors.
4. **Record as you go.** Create a node with its edges in one call. Write
   `summary` as 1-3 self-contained sentences: it is what future agents read first.
   Store long content elsewhere and point to it (`Document.uri`).
5. **Leave `status` alone on create.** New nodes start as `proposed` so a human can accept
   them. Set `done` / `archived` only when that is clearly true.
6. **Check after bulk changes.** `mem validate` reports any violations in the whole graph.

## Commands

| Command | Purpose |
|---|---|
| `mem describe [--class C] [--format md\|json]` | Ontology guide |
| `mem remember <Class> -j '<json>'` or `-p key=value ...` | Create/update a node and its edges (upsert by `id`) |
| `mem link <from_id> <edge> <to_id> [-p role=admin]` | Add an edge (single-valued edges are replaced) |
| `mem unlink <from_id> <edge> <to_id>` | Remove an edge (refused if the node requires it) |
| `mem recall <Class> [-w slot=value ...] [--limit N]` | Exact-match listing; an edge slot filters by target id |
| `mem search "<text>" [--class C]` | Substring search over ids and text fields |
| `mem get <id> [--depth 1-3]` | Node plus neighbours |
| `mem context <id>` | Working context for a node |
| `mem validate` | Check the whole graph |
| `mem init` | Create database constraints (once per database) |

Archived nodes are hidden from `recall`, `search` and `context` unless you pass `--include-archived`.

## Payload rules

- Omit `id` on create to get one generated (returned in `node.id`). Pass it to update.
- Edge slots go in the same payload as properties: a target id, a list of ids, or
  `{"id": "...", "<edge prop>": ...}` objects. An edge slot in the payload **replaces**
  that slot's edges; omitted edge slots are kept.
- `null` clears an optional property. `created_*` / `updated_*` are engine-managed.
- Enum values are matched leniently (`Key Result` → `key_result`), dates are ISO (`2027-03-31`).

## Examples

```sh
mem remember Project -j '{"id":"florque","title":"Florque","summary":"Flower shop platform."}'
mem remember Goal -j '{"id":"obj-onboarding","title":"Faster onboarding","summary":"New shops sell within a day.","level":"objective","part_of":"florque"}'
mem remember Goal -j '{"id":"kr-activation","title":"Activation 40%","summary":"Share of shops with a first sale in week one.","level":"key_result","target":"Activation >= 40% by 2027-03-31","part_of":"obj-onboarding"}'
mem remember WorkItem -j '{"id":"init-wizard","title":"Guided setup","summary":"First-run wizard for new shops.","kind":"initiative","part_of":"florque","serves":["kr-activation"]}'
mem remember WorkItem -p title="Add payment step" -p summary="Wizard step to connect payouts." -p kind=task -p part_of=init-wizard
mem remember Actor -j '{"id":"agent-claude","title":"Claude","summary":"Coding agent.","kind":"agent","member_of":[{"id":"florque","role":"contributor"}]}'
mem context init-wizard
```

Set `MEMORY_ACTOR=<your actor id>` in the command environment to attribute writes
(`created_by` / `updated_by`) to your own Actor node.
