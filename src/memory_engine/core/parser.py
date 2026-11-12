"""Interpreter for the subset of LinkML that defines a memory ontology.

Classes become node labels; slots annotated `edge: true` become relationships.
Constructs outside the supported subset raise OntologyError instead of being
ignored, so a schema never means something different here than in LinkML.

Workgraph-specific annotations understood on enum values of a class's
discriminator attribute (e.g. WorkItem.kind):
  allowed_parent: "Class" or "Class:value", comma separated — restricts the
                  containment edge (`part_of`) for nodes with that value.
  requires:       comma-separated edge slots that must be present.
On slots:
  edge: true                       the slot is a relationship.
  edge_properties: "role: Role (required)"   typed properties carried on the edge.
  containment: true                marks the containment slot (defaults to `part_of`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from .errors import OntologyError, ValidationError

# LinkML builtin types -> casting kind (see casting.py).
TYPE_KINDS = {
    "string": "string",
    "str": "string",
    "uri": "string",
    "uriorcurie": "string",
    "curie": "string",
    "ncname": "string",
    "integer": "integer",
    "float": "float",
    "double": "float",
    "decimal": "float",
    "boolean": "boolean",
    "date": "date",
    "datetime": "datetime",
}

_SCHEMA_KEYS = {
    "id", "name", "title", "description", "version", "license", "prefixes",
    "default_prefix", "default_range", "imports", "enums", "slots", "classes",
    "comments", "see_also",
}
_SLOT_KEYS = {
    "description", "title", "identifier", "required", "range", "multivalued",
    "annotations", "any_of", "comments", "examples",
}
_USAGE_KEYS = {"description", "range", "any_of", "required", "multivalued", "annotations"}
_CLASS_KEYS = {
    "description", "title", "is_a", "abstract", "slots", "slot_usage", "attributes",
    "rules", "annotations", "comments",
}
_ENUM_KEYS = {"description", "title", "permissible_values", "annotations", "comments"}
_VALUE_KEYS = {"description", "title", "annotations", "meaning", "comments"}
_RULE_KEYS = {"description", "preconditions", "postconditions"}
_EDGE_PROP = re.compile(r"^\s*(\w+)\s*:\s*(\w+)\s*(\(required\))?\s*$")


@dataclass(frozen=True)
class EdgeProperty:
    name: str
    range: str
    required: bool


@dataclass
class Slot:
    name: str
    description: str = ""
    range: str | None = None
    any_of: tuple[str, ...] = ()
    required: bool = False
    multivalued: bool = False
    identifier: bool = False
    is_edge: bool = False
    edge_properties: dict[str, EdgeProperty] = field(default_factory=dict)
    annotations: dict[str, Any] = field(default_factory=dict)

    @property
    def ranges(self) -> tuple[str, ...]:
        if self.any_of:
            return self.any_of
        return (self.range,) if self.range else ()

    @property
    def rel_type(self) -> str:
        return self.name.upper()


@dataclass
class PermissibleValue:
    name: str
    description: str = ""
    annotations: dict[str, Any] = field(default_factory=dict)


@dataclass
class EnumDef:
    name: str
    description: str
    values: dict[str, PermissibleValue]


@dataclass(frozen=True)
class Rule:
    description: str
    when: dict[str, str]  # slot -> equals_string
    require: tuple[str, ...]

    def condition(self) -> str:
        return ", ".join(f"{k}={v}" for k, v in self.when.items())


@dataclass(frozen=True)
class ParentRef:
    """An allowed edge target: a class, optionally narrowed to one discriminator value."""

    cls: str
    value: str | None = None

    def __str__(self) -> str:
        return f"{self.cls}:{self.value}" if self.value else self.cls


@dataclass
class ClassDef:
    name: str
    description: str
    is_a: str | None
    abstract: bool
    annotations: dict[str, Any]
    slots: dict[str, Slot]
    rules: list[Rule]
    discriminator: str | None

    @property
    def properties(self) -> list[Slot]:
        return [s for s in self.slots.values() if not s.is_edge]

    @property
    def edges(self) -> list[Slot]:
        return [s for s in self.slots.values() if s.is_edge]


@dataclass
class Ontology:
    name: str
    title: str
    description: str
    classes: dict[str, ClassDef]
    enums: dict[str, EnumDef]
    containment: str | None
    root: str | None
    identifier: str = "id"
    source: Path | None = None

    def concrete_classes(self) -> list[ClassDef]:
        return [c for c in self.classes.values() if not c.abstract]

    def get_class(self, name: str, *, concrete: bool = True) -> ClassDef:
        """Case-insensitive lookup for user/agent input."""
        candidates = [c for c in self.classes.values() if not (concrete and c.abstract)]
        for cls in candidates:
            if cls.name.lower() == str(name).strip().lower():
                return cls
        known = ", ".join(sorted(c.name for c in candidates))
        raise ValidationError(f"Unknown class `{name}`. Known classes: {known}")

    def is_subclass(self, child: str, parent: str) -> bool:
        current: str | None = child
        while current is not None:
            if current == parent:
                return True
            current = self.classes[current].is_a if current in self.classes else None
        return False

    def concrete_subclasses(self, name: str) -> list[str]:
        return [c.name for c in self.concrete_classes() if self.is_subclass(c.name, name)]

    def slot_for_rel(self, rel_type: str) -> str | None:
        for cls in self.classes.values():
            for slot in cls.edges:
                if slot.rel_type == rel_type:
                    return slot.name
        return None

    def range_kind(self, range_name: str) -> str:
        if range_name in self.classes:
            return "class"
        if range_name in self.enums:
            return "enum"
        if range_name in TYPE_KINDS:
            return "type"
        return "unknown"

    def discriminator_values(self, cls_name: str) -> list[str]:
        cls = self.classes[cls_name]
        if cls.discriminator is None:
            return []
        return list(self.enums[cls.slots[cls.discriminator].ranges[0]].values)

    def value_annotations(self, cls_name: str, value: Any) -> dict[str, Any]:
        cls = self.classes[cls_name]
        if cls.discriminator is None or value is None:
            return {}
        enum = self.enums[cls.slots[cls.discriminator].ranges[0]]
        pv = enum.values.get(str(value))
        return pv.annotations if pv else {}

    def allowed_targets(self, cls_name: str, slot_name: str, value: Any = None) -> list[ParentRef]:
        """Where `slot_name` may point for a node of `cls_name` with discriminator `value`."""
        slot = self.classes[cls_name].slots[slot_name]
        if slot_name == self.containment:
            allowed = self.value_annotations(cls_name, value).get("allowed_parent")
            if allowed:
                return parse_parent_refs(allowed)
        return [ParentRef(r) for r in slot.ranges]

    def required_edges(self, cls_name: str, value: Any = None) -> list[str]:
        names = [s.name for s in self.classes[cls_name].edges if s.required]
        for name in split_list(self.value_annotations(cls_name, value).get("requires")):
            if name not in names:
                names.append(name)
        return names

    def matches(self, node_cls: str, node_props: dict[str, Any], ref: ParentRef) -> bool:
        if node_cls not in self.classes or not self.is_subclass(node_cls, ref.cls):
            return False
        if ref.value is None:
            return True
        disc = self.classes[node_cls].discriminator
        return disc is not None and node_props.get(disc) == ref.value


def split_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def parse_parent_refs(raw: Any) -> list[ParentRef]:
    refs = []
    for part in split_list(raw):
        cls, _, value = part.partition(":")
        refs.append(ParentRef(cls.strip(), value.strip() or None))
    return refs


# ---------------------------------------------------------------- loading


def load_ontology(path: str | Path) -> Ontology:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise OntologyError(f"Cannot read ontology {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise OntologyError(f"Ontology {path} is not valid YAML: {exc}") from exc
    return parse_ontology(data, source=path)


def parse_ontology(data: Any, source: Path | None = None) -> Ontology:
    if not isinstance(data, dict):
        raise OntologyError("Ontology must be a YAML mapping")
    _check_keys("schema", data, _SCHEMA_KEYS)
    for imp in data.get("imports") or []:
        if imp != "linkml:types":
            raise OntologyError(f"Unsupported import `{imp}`: only linkml:types is available")

    default_range = data.get("default_range") or "string"
    enums = {str(n): _parse_enum(str(n), spec) for n, spec in _mapping(data, "enums").items()}
    slots = {
        str(n): _parse_slot(f"slot `{n}`", str(n), spec)
        for n, spec in _mapping(data, "slots").items()
    }
    raw_classes = _mapping(data, "classes")
    classes = {
        str(n): _induce(str(n), raw_classes, slots, enums, default_range) for n in raw_classes
    }

    containment = next(
        (s.name for s in slots.values() if s.is_edge and _truthy(s.annotations.get("containment"))),
        None,
    )
    if containment is None and "part_of" in slots and slots["part_of"].is_edge:
        containment = "part_of"
    root = next(
        (
            c.name
            for c in classes.values()
            if c.annotations.get("branch") == "root" or _truthy(c.annotations.get("root"))
        ),
        None,
    )
    ontology = Ontology(
        name=str(data.get("name") or "ontology"),
        title=_text(data.get("title")) or str(data.get("name") or "Ontology"),
        description=_text(data.get("description")),
        classes=classes,
        enums=enums,
        containment=containment,
        root=root,
        source=source,
    )
    ontology.identifier = _validate(ontology)
    return ontology


def _check_keys(where: str, spec: dict, allowed: set[str]) -> None:
    unknown = sorted(set(map(str, spec)) - allowed)
    if unknown:
        raise OntologyError(
            f"{where} uses unsupported key(s) {', '.join(unknown)}; "
            f"supported: {', '.join(sorted(allowed))}"
        )


def _mapping(data: dict, key: str) -> dict:
    value = data.get(key) or {}
    if not isinstance(value, dict):
        raise OntologyError(f"`{key}` must be a mapping")
    return value


def _spec(where: str, spec: Any) -> dict:
    if spec is None:
        return {}
    if not isinstance(spec, dict):
        raise OntologyError(f"{where} must be a mapping")
    return spec


def _text(value: Any) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() == "true"


def _annotations(where: str, raw: Any) -> dict[str, Any]:
    """Normalize LinkML annotations: `{tag: value}` or `{tag: {value: ...}}`."""
    spec = _spec(f"{where} annotations", raw)
    out = {}
    for tag, value in spec.items():
        if isinstance(value, dict):
            if "value" not in value:
                raise OntologyError(f"{where} annotation `{tag}` must be a scalar or have `value`")
            value = value["value"]
        out[str(tag)] = value
    return out


def _parse_any_of(where: str, raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not raw:
        raise OntologyError(f"{where} any_of must be a non-empty list")
    ranges = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"range"}:
            raise OntologyError(f"{where} any_of entries must be `{{range: X}}`")
        ranges.append(str(item["range"]))
    return tuple(ranges)


def _parse_edge_props(where: str, raw: Any) -> dict[str, EdgeProperty]:
    props = {}
    for part in split_list(raw):
        match = _EDGE_PROP.match(part)
        if not match:
            raise OntologyError(
                f"{where} edge_properties entry `{part}` must look like `name: Range (required)`"
            )
        name, rng, required = match.groups()
        props[name] = EdgeProperty(name, rng, bool(required))
    return props


def _parse_slot(where: str, name: str, raw: Any) -> Slot:
    spec = _spec(where, raw)
    _check_keys(where, spec, _SLOT_KEYS)
    annotations = _annotations(where, spec.get("annotations"))
    if "range" in spec and "any_of" in spec:
        raise OntologyError(f"{where} sets both range and any_of")
    return Slot(
        name=name,
        description=_text(spec.get("description")),
        range=str(spec["range"]) if spec.get("range") else None,
        any_of=_parse_any_of(where, spec.get("any_of")),
        required=bool(spec.get("required", False)),
        multivalued=bool(spec.get("multivalued", False)),
        identifier=bool(spec.get("identifier", False)),
        is_edge=_truthy(annotations.get("edge")),
        edge_properties=_parse_edge_props(where, annotations.get("edge_properties")),
        annotations=annotations,
    )


def _apply_usage(where: str, base: Slot, raw: Any) -> Slot:
    usage = _spec(where, raw)
    _check_keys(where, usage, _USAGE_KEYS)
    if "range" in usage and "any_of" in usage:
        raise OntologyError(f"{where} sets both range and any_of")
    changes: dict[str, Any] = {}
    if "range" in usage:
        changes.update(range=str(usage["range"]), any_of=())
    if "any_of" in usage:
        changes.update(range=None, any_of=_parse_any_of(where, usage["any_of"]))
    if "required" in usage:
        changes["required"] = bool(usage["required"])
    if "multivalued" in usage:
        changes["multivalued"] = bool(usage["multivalued"])
    if "description" in usage:
        changes["description"] = _text(usage["description"])
    if "annotations" in usage:
        annotations = {**base.annotations, **_annotations(where, usage["annotations"])}
        changes.update(
            annotations=annotations,
            is_edge=_truthy(annotations.get("edge")),
            edge_properties=_parse_edge_props(where, annotations.get("edge_properties")),
        )
    return replace(base, **changes)


def _parse_enum(name: str, raw: Any) -> EnumDef:
    where = f"enum `{name}`"
    spec = _spec(where, raw)
    _check_keys(where, spec, _ENUM_KEYS)
    pvs = spec.get("permissible_values") or {}
    if isinstance(pvs, list):
        pvs = {v: None for v in pvs}
    if not isinstance(pvs, dict) or not pvs:
        raise OntologyError(f"{where} needs permissible_values")
    values = {}
    for value, pv_raw in pvs.items():
        pv_where = f"{where} value `{value}`"
        pv_spec = _spec(pv_where, pv_raw)
        _check_keys(pv_where, pv_spec, _VALUE_KEYS)
        values[str(value)] = PermissibleValue(
            str(value), _text(pv_spec.get("description")), _annotations(pv_where, pv_spec.get("annotations"))
        )
    return EnumDef(name, _text(spec.get("description")), values)


def _slot_conditions(where: str, raw: Any) -> dict[str, dict]:
    block = _spec(where, raw)
    _check_keys(where, block, {"slot_conditions"})
    conditions = _spec(f"{where} slot_conditions", block.get("slot_conditions"))
    return {str(k): _spec(f"{where} `{k}`", v) for k, v in conditions.items()}


def _parse_rule(where: str, raw: Any) -> Rule:
    spec = _spec(where, raw)
    _check_keys(where, spec, _RULE_KEYS)
    when = {}
    for slot, cond in _slot_conditions(f"{where} preconditions", spec.get("preconditions")).items():
        _check_keys(f"{where} precondition `{slot}`", cond, {"equals_string"})
        if "equals_string" not in cond:
            raise OntologyError(f"{where} precondition `{slot}` needs equals_string")
        when[slot] = str(cond["equals_string"])
    require = []
    for slot, cond in _slot_conditions(f"{where} postconditions", spec.get("postconditions")).items():
        _check_keys(f"{where} postcondition `{slot}`", cond, {"required"})
        if cond.get("required") is not True:
            raise OntologyError(f"{where} postcondition `{slot}`: only `required: true` is supported")
        require.append(slot)
    if not require:
        raise OntologyError(f"{where} has no postconditions")
    return Rule(_text(spec.get("description")), when, tuple(require))


def _lineage(name: str, raw_classes: dict) -> list[str]:
    """`name` and its is_a ancestors, root first."""
    chain: list[str] = []
    current: str | None = name
    while current is not None:
        if current in chain:
            raise OntologyError("is_a cycle: " + " -> ".join(chain + [current]))
        if current not in raw_classes:
            raise OntologyError(f"class `{chain[-1]}` is_a undefined class `{current}`")
        chain.append(current)
        parent = _spec(f"class `{current}`", raw_classes[current]).get("is_a")
        current = str(parent) if parent is not None else None
    return chain[::-1]


def _induce(
    name: str, raw_classes: dict, slots: dict[str, Slot], enums: dict[str, EnumDef], default_range: str
) -> ClassDef:
    induced: dict[str, Slot] = {}
    rules: list[Rule] = []
    attributes: list[str] = []
    for cname in _lineage(name, raw_classes):
        where = f"class `{cname}`"
        spec = _spec(where, raw_classes[cname])
        _check_keys(where, spec, _CLASS_KEYS)
        for sname in spec.get("slots") or []:
            if sname not in slots:
                raise OntologyError(f"{where} uses undefined slot `{sname}`")
            induced.setdefault(sname, slots[sname])
        for aname, aspec in (spec.get("attributes") or {}).items():
            induced[str(aname)] = _parse_slot(f"{where} attribute `{aname}`", str(aname), aspec)
            attributes.append(str(aname))
        for uname, uspec in (spec.get("slot_usage") or {}).items():
            if uname not in induced:
                raise OntologyError(f"{where} has slot_usage for `{uname}`, which it does not have")
            induced[uname] = _apply_usage(f"{where} slot_usage `{uname}`", induced[uname], uspec)
        rules += [_parse_rule(f"{where} rule {i + 1}", r) for i, r in enumerate(spec.get("rules") or [])]

    for sname, slot in induced.items():
        if not slot.is_edge and not slot.ranges:
            induced[sname] = replace(slot, range=default_range)

    spec = _spec(f"class `{name}`", raw_classes[name])
    discriminator = next(
        (a for a in attributes if induced[a].required and induced[a].range in enums), None
    )
    return ClassDef(
        name=name,
        description=_text(spec.get("description")),
        is_a=str(spec["is_a"]) if spec.get("is_a") else None,
        abstract=bool(spec.get("abstract", False)),
        annotations=_annotations(f"class `{name}`", spec.get("annotations")),
        slots=induced,
        rules=rules,
        discriminator=discriminator,
    )


# ---------------------------------------------------------------- validation


def _validate(ont: Ontology) -> str:
    """Check the parsed ontology for consistency. Returns the identifier slot name."""
    errors: list[str] = []
    identifiers: set[str] = set()
    for cls in ont.classes.values():
        where = f"class `{cls.name}`"
        for slot in cls.slots.values():
            for rng in slot.ranges:
                kind = ont.range_kind(rng)
                if kind == "unknown":
                    errors.append(f"{where} slot `{slot.name}` has unknown range `{rng}`")
                elif slot.is_edge and kind != "class":
                    errors.append(f"{where} edge `{slot.name}` must range over classes, not `{rng}`")
                elif not slot.is_edge and kind == "class":
                    errors.append(
                        f"{where} slot `{slot.name}` ranges over class `{rng}` "
                        "but is not annotated `edge: true`"
                    )
            if slot.is_edge and not slot.ranges and not cls.abstract:
                errors.append(
                    f"{where} edge `{slot.name}` has no target class; set range or any_of in slot_usage"
                )
            if slot.is_edge and slot.identifier:
                errors.append(f"{where} identifier `{slot.name}` cannot be an edge")
            for prop in slot.edge_properties.values():
                if ont.range_kind(prop.range) not in ("enum", "type"):
                    errors.append(
                        f"{where} edge `{slot.name}` property `{prop.name}` has invalid range `{prop.range}`"
                    )
        if cls.abstract:
            continue

        ids = [s.name for s in cls.slots.values() if s.identifier]
        if len(ids) != 1:
            errors.append(f"{where} must have exactly one identifier slot, found {len(ids)}")
        identifiers.update(ids)

        for i, rule in enumerate(cls.rules, 1):
            for sname, expected in rule.when.items():
                slot = cls.slots.get(sname)
                if slot is None or slot.is_edge:
                    errors.append(f"{where} rule {i} precondition on unknown property `{sname}`")
                elif slot.ranges[0] in ont.enums and expected not in ont.enums[slot.ranges[0]].values:
                    errors.append(f"{where} rule {i} compares `{sname}` to unknown value `{expected}`")
            for sname in rule.require:
                if sname not in cls.slots:
                    errors.append(f"{where} rule {i} requires unknown slot `{sname}`")

        for value in ont.discriminator_values(cls.name):
            vwhere = f"{where} {cls.discriminator}={value}"
            annotations = ont.value_annotations(cls.name, value)
            if "allowed_parent" in annotations:
                if ont.containment is None or ont.containment not in cls.slots:
                    errors.append(f"{vwhere} sets allowed_parent but the class has no containment slot")
                else:
                    ranges = cls.slots[ont.containment].ranges
                    for ref in parse_parent_refs(annotations["allowed_parent"]):
                        if ref.cls not in ont.classes:
                            errors.append(f"{vwhere} allowed_parent names unknown class `{ref.cls}`")
                            continue
                        if ref.value is not None and ref.value not in ont.discriminator_values(ref.cls):
                            errors.append(f"{vwhere} allowed_parent names unknown value `{ref}`")
                        if not any(ont.is_subclass(ref.cls, r) for r in ranges):
                            errors.append(
                                f"{vwhere} allowed_parent `{ref}` is outside the "
                                f"`{ont.containment}` range ({', '.join(ranges)})"
                            )
            for sname in split_list(annotations.get("requires")):
                if sname not in cls.slots or not cls.slots[sname].is_edge:
                    errors.append(f"{vwhere} requires `{sname}`, which is not an edge of the class")

    if not ont.concrete_classes():
        errors.append("ontology defines no concrete classes")
    if len(identifiers) > 1:
        errors.append(f"all classes must share one identifier slot, found: {', '.join(sorted(identifiers))}")
    if not errors:
        errors += _grounding_errors(ont)
    if errors:
        raise OntologyError("Invalid ontology", errors)
    return identifiers.pop()


def _static_requirements(ont: Ontology, cls_name: str, value: str | None) -> list[str]:
    """Edge slots a node of this class/value must always have."""
    cls = ont.classes[cls_name]
    required = ont.required_edges(cls_name, value)
    for rule in cls.rules:
        applies = not rule.when or (
            cls.discriminator is not None and rule.when == {cls.discriminator: value}
        )
        if applies:
            required += [r for r in rule.require if cls.slots[r].is_edge and r not in required]
    return required


def _grounding_errors(ont: Ontology) -> list[str]:
    """Find classes whose required edges can only be satisfied by circular chains.

    A (class, discriminator value) state is creatable when every required edge
    can point at some already-creatable state. Iterating to a fixpoint leaves
    exactly the states caught in requirement loops (e.g. A requires part_of A).
    """
    states = [
        (c.name, v) for c in ont.concrete_classes() for v in (ont.discriminator_values(c.name) or [None])
    ]
    grounded: set[tuple[str, str | None]] = set()

    def satisfiable(cls_name: str, value: str | None, slot: str) -> bool:
        return any(
            ont.is_subclass(gc, ref.cls) and (ref.value is None or gv == ref.value)
            for ref in ont.allowed_targets(cls_name, slot, value)
            for gc, gv in grounded
        )

    changed = True
    while changed:
        changed = False
        for state in states:
            if state in grounded:
                continue
            if all(satisfiable(*state, slot) for slot in _static_requirements(ont, *state)):
                grounded.add(state)
                changed = True

    errors = []
    for cls_name, value in states:
        if (cls_name, value) not in grounded:
            label = f"{cls_name}:{value}" if value else cls_name
            required = ", ".join(_static_requirements(ont, cls_name, value))
            errors.append(
                f"`{label}` can never be created: its required edges ({required}) only lead "
                "to classes that cannot be created either (circular requirement)"
            )
    return errors
