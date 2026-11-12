"""Render the ontology as guidance for agents, so they know the memory's boundaries before writing."""

from __future__ import annotations

from typing import Any

from .engine import DEFAULT_STATUS, STATUS_SLOT, SYSTEM_PROPERTIES
from .parser import ClassDef, Ontology, Slot, split_list


def _cell(text: Any) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ")


def _range(ont: Ontology, slot: Slot) -> str:
    return " \\| ".join(slot.ranges)


def _class_markdown(ont: Ontology, cls: ClassDef) -> list[str]:
    ann = cls.annotations
    lines = [f"### {cls.name}", "", cls.description, ""]
    for key, label in (("branch", "Branch"), ("use_when", "Use when"), ("not_for", "Not for")):
        if ann.get(key):
            lines.append(f"- **{label}:** {' '.join(str(ann[key]).split())}")
    lines.append("")

    lines += ["**Properties**", "", "| name | type | required | description |", "|---|---|---|---|"]
    for slot in cls.properties:
        if slot.name in SYSTEM_PROPERTIES:
            continue
        required = "auto if omitted" if slot.identifier else ("yes" if slot.required else "")
        if slot.name == STATUS_SLOT and slot.required:
            required = f"defaults to `{DEFAULT_STATUS}`"
        multi = " (list)" if slot.multivalued else ""
        lines.append(f"| `{slot.name}` | {_range(ont, slot)}{multi} | {required} | {_cell(slot.description)} |")
    lines.append("")

    if cls.edges:
        lines += ["**Edges** (outgoing; pass target ids)", "", "| slot | targets | cardinality | required | description |", "|---|---|---|---|---|"]
        for slot in cls.edges:
            cardinality = "many" if slot.multivalued else "one"
            extra = ""
            if slot.edge_properties:
                props = ", ".join(
                    f"`{p.name}`: {p.range}{' (required)' if p.required else ''}"
                    for p in slot.edge_properties.values()
                )
                extra = f" Edge properties: {props}."
            required = "yes" if slot.required else ""
            lines.append(
                f"| `{slot.name}` | {_range(ont, slot)} | {cardinality} | {required} | {_cell(slot.description)}{extra} |"
            )
        lines.append("")

    if cls.discriminator:
        enum = ont.enums[cls.slots[cls.discriminator].ranges[0]]
        lines += [f"**`{cls.discriminator}` values** (required)", ""]
        for pv in enum.values.values():
            notes = []
            if pv.annotations.get("allowed_parent"):
                notes.append(f"{ont.containment} → {pv.annotations['allowed_parent'].replace(',', ' or ')}")
            if pv.annotations.get("requires"):
                notes.append(f"requires {', '.join(split_list(pv.annotations['requires']))}")
            suffix = f" _({'; '.join(notes)})_" if notes else ""
            lines.append(f"- `{pv.name}`: {pv.description}{suffix}")
        lines.append("")

    if cls.rules:
        lines += ["**Rules**", ""]
        for rule in cls.rules:
            when = f"when {rule.condition()}, " if rule.when else ""
            lines.append(f"- {rule.description} ({when}{', '.join(rule.require)} required)")
        lines.append("")
    return lines


def render_markdown(ont: Ontology, class_name: str | None = None) -> str:
    classes = [ont.get_class(class_name)] if class_name else ont.concrete_classes()
    lines = [f"# Memory ontology: {ont.title}", ""]
    if not class_name:
        lines += [
            ont.description,
            "",
            "## How writes work",
            "",
            f"- Nodes are identified by `{ont.identifier}`. Omit it on create to get a generated id; "
            "pass it to update (upsert, never duplicates).",
            "- One `remember` call sets properties and outgoing edges together. "
            "An edge slot given in the payload replaces that slot's edges; omitted edge slots are kept.",
            "- Edge values are a target id, a list of ids, or `{\"id\": ..., <edge props>}` objects.",
            "- Unknown slots, wrong enum values and broken rules are rejected with the reason.",
            "- New nodes get `status: proposed` unless you set it. Set `null` to clear an optional property.",
            f"- Engine-managed: {', '.join(f'`{p}`' for p in sorted(SYSTEM_PROPERTIES))}.",
        ]
        if ont.root:
            lines.append(
                f"- `{ont.root}` is the root. Most other nodes hang off it through `{ont.containment}`."
            )
        lines += ["", "## Classes", ""]
    for cls in classes:
        lines += _class_markdown(ont, cls)

    if not class_name:
        used = {
            s.ranges[0]
            for c in classes
            for s in c.properties
            if s.ranges and s.ranges[0] in ont.enums and s.name != c.discriminator
        }
        used |= {p.range for c in classes for s in c.edges for p in s.edge_properties.values() if p.range in ont.enums}
        if used:
            lines += ["## Shared enums", ""]
            for name in sorted(used):
                enum = ont.enums[name]
                lines += [f"### {name}", ""]
                if enum.description:
                    lines += [enum.description, ""]
                lines += [f"- `{pv.name}`: {pv.description}" for pv in enum.values.values()]
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _slot_json(slot: Slot) -> dict[str, Any]:
    data: dict[str, Any] = {
        "range": list(slot.ranges),
        "required": slot.required,
        "multivalued": slot.multivalued,
        "description": slot.description,
    }
    if slot.identifier:
        data["identifier"] = True
    if slot.edge_properties:
        data["edge_properties"] = {
            p.name: {"range": p.range, "required": p.required} for p in slot.edge_properties.values()
        }
    return data


def describe_json(ont: Ontology, class_name: str | None = None) -> dict[str, Any]:
    classes = [ont.get_class(class_name)] if class_name else ont.concrete_classes()
    result: dict[str, Any] = {
        "ontology": ont.name,
        "title": ont.title,
        "identifier": ont.identifier,
        "containment": ont.containment,
        "root": ont.root,
        "classes": {},
        "enums": {
            e.name: {pv.name: {"description": pv.description, **pv.annotations} for pv in e.values.values()}
            for e in ont.enums.values()
        },
    }
    for cls in classes:
        result["classes"][cls.name] = {
            "description": cls.description,
            "annotations": cls.annotations,
            "discriminator": cls.discriminator,
            "properties": {s.name: _slot_json(s) for s in cls.properties if s.name not in SYSTEM_PROPERTIES},
            "edges": {s.name: {**_slot_json(s), "rel_type": s.rel_type} for s in cls.edges},
            "rules": [{"description": r.description, "when": r.when, "require": list(r.require)} for r in cls.rules],
        }
    return result
