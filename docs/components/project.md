# Project merge (`core/project.py`)

Combines a work ontology and a domain ontology into one `Ontology`, which the rest of the engine
then uses unchanged. Standalone ontologies never go through this module.

## Entry points

- `load_project(manifest_path)`: reads the manifest (`config.load_manifest`), loads both ontologies and
  calls `merge_ontologies`. Used by the CLI and by `skill build-project`.
- `merge_ontologies(name, work, domain, prefix="dom")`: the merge itself.

## Steps

1. **Identifier check.** Both ontologies must use the same identifier slot.
2. **Prefix the domain** (`prefix_ontology`). Every domain class, enum and edge slot becomes
   `<prefix>_<name>`. Each reference follows the rename: `is_a`, class and enum ranges, `any_of`, edge
   property ranges, rule `require`, `requires` and `allowed_parent` annotations, containment and root.
   Property names are not renamed. The prefix must start with a letter and contain only letters and digits.
3. **Check clashes.** Class and enum names must not clash across the two modules. After prefixing this
   only happens if the work side already uses a prefixed name.
4. **Expand bridge roles** (`_expand_bridges`). For each work edge with a `target_role`, the domain
   classes tagged with that role become its `any_of` targets. A role nobody tags leaves the edge without
   targets, so it does not exist in the graph.
5. **Combine.** Work classes, then domain classes. Enums are merged. Containment and root come from the
   work ontology. The merged `annotations["modules"]` records the work name, the domain name and the
   prefix, which the identity stamp uses.

## Naming rules in the merged graph

- Same-named edges share one relationship table, with their source and target pairs unioned. Two
  modules can therefore both have an edge called `serves` without a duplicate table.
- The work ontology's names are never changed. A standalone workgraph database and a project's work
  data are identical.

## Deliberate limits

- No cross-module cycle checks: bridges are optional, so they cannot make a class uncreatable.
- Domain containment is not the merged containment. A domain `part_of` becomes `dom_part_of` and does
  not drive `mem context` or cycle checks.
- Module versions are not recorded.

## Tests

`tests/unit/test_project.py` covers role expansion, dropped roles, optional bridges, unknown roles,
prefixing of every reference kind, clash errors, identifier mismatch, bare-name lookup, the manifest
path and the skill's self-contained copy.

## Related

- [Ontology parser](parser.md): the bridge vocabulary and the per-module checks.
- [Project mode](../guides/project-mode.md): what users see.
