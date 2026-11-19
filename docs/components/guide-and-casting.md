# Guide and casting (`core/guide.py`, `core/casting.py`)

Two modules that turn the ontology into text an agent can read, and loose input into typed values.

## Guide (`guide.py`)

- `render_markdown(ontology, class_name=None)`: the ontology guide, as Markdown. Used by
  `references/ontology.md` and by `mem describe`.
- `describe_json(ontology, class_name=None)`: the same content as JSON.
- Per class: description, `use_when` / `not_for` notes, slots with ranges, edges with targets and
  properties, and the rules.
- `default_status(ontology)` and `supports_archiving(ontology)` tell the skill which defaults apply.

Guides leave out bridges that have no resolved targets, so a standalone workgraph does not advertise
`targets`.

## Casting (`casting.py`)

Agents send strings and JSON. Casting converts them to the declared types before validation.

| Range | Accepts |
|---|---|
| `string` | Scalars converted to text |
| `integer`, `float` | Numbers and numeric strings |
| `boolean` | `true`/`false`, `yes`/`no`, `1`/`0` |
| `date`, `datetime` | ISO dates and timestamps |
| enum | Values matched leniently: case, spaces and hyphens are normalised (`In Progress` becomes `in_progress`) |

`cast_slot` honours multivalued slots and `any_of` ranges. `cast_filter` does the same for read filters.
A value that cannot be cast raises `CastError`, which the engine reports as a validation issue.

## Related

- [Engine](engine.md): where casting is applied.
- [Skill generation](skill.md): where the guide text is used.
