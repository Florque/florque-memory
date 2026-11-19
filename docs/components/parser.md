# Ontology parser (`core/parser.py`)

Reads a LinkML ontology (YAML) and produces an `Ontology` model. The parser accepts only a documented
subset of LinkML. Anything outside it is rejected at load time instead of being ignored, so a schema
never means something different here than it does in LinkML.

## Supported subset

- Schema: `id`, `name`, `title`, `description`, `prefixes`, `default_range`, `imports` (only
  `linkml:types`), `annotations`.
- Classes: `is_a`, `abstract`, `slots`, `attributes`, `slot_usage`, `rules`, `annotations`.
- Slots: `identifier`, `required`, `range`, `any_of`, `multivalued`, `annotations`.
- Enums with `permissible_values` and annotations.
- Types: `string`, `integer`, `float`, `double`, `decimal`, `boolean`, `date`, `datetime`, `uri`,
  `uriorcurie`, `curie`, `ncname`.

## The model

| Type | Holds |
|---|---|
| `Ontology` | name, classes, enums, containment slot, root class, identifier, annotations |
| `ClassDef` | slots (induced through `is_a`), rules, discriminator, `properties`, `edges`, `bridges` |
| `Slot` | range or `any_of`, required, multivalued, edge flag, edge properties, annotations |
| `EnumDef` / `PermissibleValue` | allowed values and their annotations |
| `Rule` | when-conditions on a discriminator value and the required slots |

An **edge** is a slot annotated `edge: true`. Its relationship type is the slot name in upper case.

## Bridge roles

- `BRIDGE_ROLES = (system, component, finding, decision, data)` is the closed vocabulary.
- A domain class declares `bridge_role` as an annotation (comma-separated).
- A work edge declares `target_role` and must be optional. It has no class range; merging gives it
  one (see [project merge](project.md)).
- `ClassDef.edges` leaves out an edge that has no targets. `ClassDef.bridges` lists every bridge,
  resolved or not.

## Load-time checks

Checked for every ontology:

- unknown ranges and slots, and `is_a` cycles;
- an edge that does not range over classes, or a class range without `edge: true`;
- each concrete class has exactly one identifier, and all classes share it;
- `allowed_parent` names real classes and values inside the containment range;
- `requires` names real edges;
- **circular requirements**: classes whose required edges can only be satisfied by classes that can
  never be created. Found by a fixpoint over (class, discriminator value) states.
- bridge roles and `target_role` values are in the vocabulary, and bridges are optional.

## Lookup

`Ontology.get_class(name)` is case-insensitive. Abstract classes are excluded by default. In a project
it also resolves a bare domain name to its prefixed class (`Component` to `dom_Component`) when that
is the only match. An exact match always wins.

## Related

- [Schema](schema.md): how the model becomes tables.
- [Project merge](project.md): where bridge roles are expanded.
