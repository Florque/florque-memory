"""Ontology -> GraphSchema: the tables a schema-first backend (Ladybug) needs.

Node tables: one per concrete class, with the identifier, every property slot and the
system columns. Rel tables: one per edge slot, with every (source, target) class pair the
ontology can ever allow, plus the declared edge properties and timestamps.
"""

from __future__ import annotations

from ..database.base import Column, GraphSchema, RelTable
from .engine import SYSTEM_PROPERTIES
from .parser import TYPE_KINDS, Ontology, Slot

SYSTEM_COLUMNS = {
    "created_at": Column("datetime"),
    "updated_at": Column("datetime"),
    "created_by": Column("string"),
    "updated_by": Column("string"),
}
EDGE_SYSTEM_COLUMNS = {"created_at": Column("datetime"), "updated_at": Column("datetime")}
assert set(SYSTEM_COLUMNS) == SYSTEM_PROPERTIES


def _column_type(ontology: Ontology, ranges: tuple[str, ...]) -> str:
    """Enums and string-like types are strings; a slot with several value ranges falls back to string."""
    kinds = {TYPE_KINDS.get(r, "string") for r in ranges if r not in ontology.enums} or {"string"}
    return kinds.pop() if len(kinds) == 1 else "string"


def _slot_column(ontology: Ontology, slot: Slot) -> Column:
    return Column(_column_type(ontology, slot.ranges or ("string",)), slot.multivalued)


def _target_labels(ontology: Ontology, cls_name: str, slot: Slot) -> list[str]:
    """Every concrete class this slot may point to, over all discriminator values."""
    values = [None, *ontology.discriminator_values(cls_name)]
    labels: list[str] = []
    for value in values:
        for ref in ontology.allowed_targets(cls_name, slot.name, value):
            if ref.cls not in ontology.classes:
                continue
            for label in ontology.concrete_subclasses(ref.cls):
                if label not in labels:
                    labels.append(label)
    return labels


def schema_for(ontology: Ontology) -> GraphSchema:
    id_key = ontology.identifier
    nodes: dict[str, dict[str, Column]] = {}
    rels: dict[str, dict] = {}
    for cls in ontology.concrete_classes():
        columns = {id_key: Column("string")}
        for slot in cls.properties:
            if slot.name != id_key and slot.name not in SYSTEM_COLUMNS:
                columns[slot.name] = _slot_column(ontology, slot)
        columns.update(SYSTEM_COLUMNS)
        nodes[cls.name] = columns

        for slot in cls.edges:
            entry = rels.setdefault(slot.rel_type, {"pairs": [], "props": {}})
            for target in _target_labels(ontology, cls.name, slot):
                if (cls.name, target) not in entry["pairs"]:
                    entry["pairs"].append((cls.name, target))
            for name, prop in slot.edge_properties.items():
                entry["props"][name] = Column(_column_type(ontology, (prop.range,)))

    rel_tables = {
        rel: RelTable(tuple(entry["pairs"]), {**entry["props"], **EDGE_SYSTEM_COLUMNS})
        for rel, entry in rels.items()
        if entry["pairs"]
    }
    return GraphSchema(id_key, nodes, rel_tables)
