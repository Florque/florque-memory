import pytest

from memory_engine.core.engine import SYSTEM_PROPERTIES
from memory_engine.core.parser import parse_ontology
from memory_engine.core.schema import schema_for
from memory_engine.database.base import Column, ident


def test_node_tables_cover_concrete_classes(ontology):
    schema = schema_for(ontology)
    assert schema.labels == [c.name for c in ontology.concrete_classes()]
    assert "Node" not in schema.node_tables  # abstract
    for columns in schema.node_tables.values():
        assert columns[ontology.identifier] == Column("string")
        assert SYSTEM_PROPERTIES <= set(columns)


def test_column_types(ontology):
    goal = schema_for(ontology).node_tables["Goal"]
    assert goal["due"] == Column("date")
    assert goal["level"] == Column("string")  # enum
    assert goal["created_at"] == Column("datetime")
    assert "part_of" not in goal  # edges are rel tables, not columns
    assert schema_for(ontology).node_tables["Signal"]["observed_at"] == Column("datetime")


def test_rel_tables(ontology):
    rels = schema_for(ontology).rel_tables
    assert set(rels) == {"PART_OF", "SERVES", "DEPENDS_ON", "ABOUT", "MEMBER_OF", "OWNED_BY"}
    assert ("Goal", "Project") in rels["PART_OF"].pairs and ("Actor", "Actor") in rels["PART_OF"].pairs
    assert ("Project", "Goal") not in rels["PART_OF"].pairs
    assert rels["MEMBER_OF"].pairs == (("Actor", "Project"),)
    assert rels["MEMBER_OF"].props["role"] == Column("string")
    assert set(rels["SERVES"].props) == {"created_at", "updated_at"}


def test_types_lists_and_abstract_targets():
    ontology = parse_ontology({
        "id": "t", "name": "t",
        "slots": {
            "id": {"identifier": True},
            "score": {"range": "integer"}, "ratio": {"range": "decimal"}, "done": {"range": "boolean"},
            "tags": {"multivalued": True}, "uri": {"range": "uri"},
            "links": {"range": "Base", "multivalued": True, "annotations": {"edge": True}},
        },
        "classes": {
            "Base": {"abstract": True, "slots": ["id"]},
            "A": {"is_a": "Base", "slots": ["score", "ratio", "done", "tags", "uri", "links"]},
            "B": {"is_a": "Base"},
        },
    })
    schema = schema_for(ontology)
    a = schema.node_tables["A"]
    assert (a["score"], a["ratio"], a["done"], a["tags"], a["uri"]) == (
        Column("integer"), Column("float"), Column("boolean"), Column("string", True), Column("string"),
    )
    assert schema.rel_tables["LINKS"].pairs == (("A", "A"), ("A", "B"))


def test_ident_rejects_injection():
    assert ident("Goal") == "`Goal`"
    for bad in ("Goal`) DETACH DELETE n //", "a b", "1x", ""):
        with pytest.raises(ValueError):
            ident(bad)
