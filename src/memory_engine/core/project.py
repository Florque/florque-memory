"""Project mode: merge a work ontology and a domain ontology into one graph.

The work ontology declares bridge edges against roles (`target_role: finding`). Domain classes
declare the roles they play (`bridge_role: finding`). Merging expands each role edge into the
domain classes tagged with that role. A role nobody tags yields no edge at all.

Namespacing: everything that comes from the domain ontology is prefixed (default `dom`):
classes, enums and edge slots, so `Component` becomes `dom_Component`, `part_of` becomes
`dom_part_of` (relationship `DOM_PART_OF`). Property (column) names stay unchanged: they live inside
their own node table and cannot clash. The work ontology is never prefixed.

Bridges are always optional, so cross-module links never make a class uncreatable. Cycle checks
stay per module, and the merged containment slot is the work ontology's.
"""

from __future__ import annotations

import re
from dataclasses import replace

from ..config import load_manifest
from .errors import OntologyError
from .parser import ClassDef, EdgeProperty, EnumDef, Ontology, PermissibleValue, Rule, Slot, load_ontology, split_list

DEFAULT_DOMAIN_PREFIX = "dom"
_PREFIX = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")


def role_targets(domain: Ontology) -> dict[str, list[str]]:
    """Bridge role -> names of the domain classes that play it, in declaration order."""
    targets: dict[str, list[str]] = {}
    for cls in domain.classes.values():
        for role in split_list(cls.annotations.get("bridge_role")):
            targets.setdefault(role, []).append(cls.name)
    return targets


def prefix_ontology(domain: Ontology, prefix: str) -> Ontology:
    """Rename every class, enum and edge slot of `domain` to `<prefix>_<name>`.

    Every reference follows: `is_a`, class and enum ranges, `any_of`, edge property ranges, rule
    `require`, `requires` and `allowed_parent` annotations, the containment slot and the root.
    """
    if not _PREFIX.match(prefix):
        raise OntologyError(f"domain prefix `{prefix}` must start with a letter and use only letters and digits")

    classes_map = {n: f"{prefix}_{n}" for n in domain.classes}
    enums_map = {n: f"{prefix}_{n}" for n in domain.enums}
    edge_names = {s.name for c in domain.classes.values() for s in c.slots.values() if s.is_edge}

    def rclass_or_enum(name: str) -> str:
        return classes_map.get(name) or enums_map.get(name) or name

    def redge(name: str) -> str:
        return f"{prefix}_{name}" if name in edge_names else name

    def rref(ref: str) -> str:
        """`Class` or `Class:value` from an allowed_parent annotation."""
        cls, sep, value = ref.strip().partition(":")
        return classes_map.get(cls, cls) + (f"{sep}{value}" if sep else "")

    def rannotations(annotations: dict) -> dict:
        out = dict(annotations)
        if "allowed_parent" in out:
            out["allowed_parent"] = ",".join(rref(r) for r in split_list(out["allowed_parent"]))
        if "requires" in out:
            out["requires"] = ",".join(redge(n) for n in split_list(out["requires"]))
        return out

    def rslot(slot: Slot) -> Slot:
        return replace(
            slot,
            name=redge(slot.name),
            range=rclass_or_enum(slot.range) if slot.range else None,
            any_of=tuple(rclass_or_enum(r) for r in slot.any_of),
            edge_properties={
                k: EdgeProperty(p.name, rclass_or_enum(p.range), p.required)
                for k, p in slot.edge_properties.items()
            },
        )

    classes: dict[str, ClassDef] = {}
    for cls in domain.classes.values():
        slots = {}
        for slot in cls.slots.values():
            renamed = rslot(slot)
            slots[renamed.name] = renamed
        rules = [
            Rule(r.description, dict(r.when), tuple(redge(n) for n in r.require)) for r in cls.rules
        ]
        new_name = classes_map[cls.name]
        classes[new_name] = replace(
            cls,
            name=new_name,
            is_a=classes_map.get(cls.is_a, cls.is_a) if cls.is_a else None,
            slots=slots,
            rules=rules,
        )

    enums: dict[str, EnumDef] = {}
    for enum in domain.enums.values():
        values = {
            v: PermissibleValue(pv.name, pv.description, rannotations(pv.annotations))
            for v, pv in enum.values.items()
        }
        enums[enums_map[enum.name]] = EnumDef(enums_map[enum.name], enum.description, values)

    return replace(
        domain,
        classes=classes,
        enums=enums,
        containment=redge(domain.containment) if domain.containment else None,
        root=classes_map.get(domain.root, domain.root) if domain.root else None,
    )


def _expand_bridges(work: Ontology, targets: dict[str, list[str]]) -> dict[str, ClassDef]:
    classes: dict[str, ClassDef] = {}
    for cls in work.classes.values():
        slots = {}
        for name, slot in cls.slots.items():
            role = slot.annotations.get("target_role")
            if not slot.is_edge or role is None:
                slots[name] = slot
                continue
            found = targets.get(str(role))
            if found:
                # The domain classes replace the abstract role; `matches` uses is_subclass,
                # so a domain subclass of a tagged class is still a valid target.
                slots[name] = replace(slot, any_of=tuple(found))
        classes[cls.name] = replace(cls, slots=slots)
    return classes


def merge_ontologies(
    name: str, work: Ontology, domain: Ontology, prefix: str = DEFAULT_DOMAIN_PREFIX
) -> Ontology:
    """One ontology for a project. Both inputs must already be valid on their own."""
    if work.identifier != domain.identifier:
        raise OntologyError(
            f"work and domain ontologies use different identifier slots "
            f"(`{work.identifier}` vs `{domain.identifier}`); they must match"
        )

    domain = prefix_ontology(domain, prefix)

    clashes = sorted(set(work.classes) & set(domain.classes))
    if clashes:
        raise OntologyError("class names clash between the work and domain ontologies: " + ", ".join(clashes))
    enum_clashes = sorted(set(work.enums) & set(domain.enums))
    if enum_clashes:
        raise OntologyError("enum names clash between the work and domain ontologies: " + ", ".join(enum_clashes))

    classes = _expand_bridges(work, role_targets(domain))
    classes.update(domain.classes)

    return Ontology(
        name=name,
        title=name,
        description=work.description,
        classes=classes,
        enums={**work.enums, **domain.enums},
        containment=work.containment,
        root=work.root,
        identifier=work.identifier,
        source=work.source,
        annotations={"modules": {"work": work.name, "domain": domain.name, "domain_prefix": prefix}},
    )


def load_project(manifest_path) -> Ontology:
    """Read a project manifest, load its work and domain ontologies, and merge them."""
    manifest = load_manifest(manifest_path)
    return merge_ontologies(
        manifest.name,
        load_ontology(manifest.work),
        load_ontology(manifest.domain),
        prefix=manifest.domain_prefix,
    )
