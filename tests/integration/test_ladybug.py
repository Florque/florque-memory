"""Engine against a real, embedded Ladybug database. Needs no server, so it always runs."""

import subprocess
import sys
import textwrap
from datetime import UTC, date

import pytest
from conftest import build_project

from memory_engine.config import Settings
from memory_engine.core.engine import OntologyEngine
from memory_engine.core.errors import (
    DatabaseError,
    NotFoundError,
    ValidationError,
)
from memory_engine.core.parser import parse_ontology
from memory_engine.core.schema import schema_for
from memory_engine.database import open_driver
from memory_engine.database.ladybug_driver import LadybugDriver

pytestmark = pytest.mark.ladybug


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "data" / "memory.lbdb"


@pytest.fixture
def driver(ontology, db_path):
    drv = LadybugDriver(db_path, ontology.identifier)
    yield drv
    drv.close()


@pytest.fixture
def engine(ontology, driver):
    eng = OntologyEngine(ontology, driver, actor="agent-it")
    eng.init()
    return eng


def rows(driver, query, **params):
    return driver._rows(query, params)


# ------------------------------------------------------------ parity with tests/integration/test_neo4j.py


def test_upsert_is_idempotent(engine, driver):
    for summary in ("First.", "Second."):
        engine.remember("Project", {"id": "p", "title": "P", "summary": summary})
    assert rows(driver, "MATCH (n:Project {id: $id}) RETURN count(n)", id="p") == [[1]]
    node = engine.recall("Project", {"id": "p"})[0]
    assert node["summary"] == "Second."
    assert node["created_by"] == "agent-it" and node["updated_by"] == "agent-it"
    assert node["created_at"] < node["updated_at"]
    assert node["created_at"].tzinfo == UTC


def test_tables_exist(engine, driver):
    tables = {name: kind for name, kind in rows(driver, "CALL show_tables() RETURN name, type")}
    assert {tables[c] for c in ("Project", "Goal", "WorkItem", "Document", "Signal", "Actor")} == {"NODE"}
    assert tables["PART_OF"] == "REL" and tables["MEMBER_OF"] == "REL"


def test_tree_edges_and_context(engine, driver):
    ids = build_project(engine)
    reached = rows(driver, "MATCH (:WorkItem {id: $t})-[:PART_OF*1..10]->(p:Project) RETURN count(p)", t=ids["task"])
    assert reached == [[1]]
    ctx = engine.context(ids["task"])
    assert [a["id"] for a in ctx["ancestors"]] == [ids["epic"], ids["initiative"], ids["project"]]
    serves = [link for link in ctx["links"] if link["slot"] == "serves"]
    assert serves and serves[0]["node"]["id"] == ids["kr"]
    assert engine.remember("Goal", {"id": ids["kr"], "due": "2027-03-31"})["node"]["due"] == date(2027, 3, 31)


def test_replace_and_link_semantics(engine, driver):
    ids = build_project(engine)
    engine.link(ids["task"], "part_of", ids["initiative"])
    assert rows(driver, "MATCH (:WorkItem {id: $t})-[r:PART_OF]->(b) RETURN b.id", t=ids["task"]) == [[ids["initiative"]]]
    engine.remember("Actor", {
        "id": "alex", "title": "Alex", "summary": "s", "kind": "person",
        "member_of": [{"id": ids["project"], "role": "admin"}],
    })
    assert rows(driver, "MATCH (:Actor {id: 'alex'})-[r:MEMBER_OF]->() RETURN r.role") == [["admin"]]
    links = engine.context("alex")["links"]
    assert [link["edge"] for link in links if link["slot"] == "member_of"] == [{"role": "admin"}]


def test_rejected_write_leaves_graph_untouched(engine, driver):
    ids = build_project(engine)
    with pytest.raises(ValidationError):
        engine.remember("WorkItem", {"id": "bad", "title": "t", "summary": "s", "kind": "task", "part_of": ids["project"]})
    assert engine.db.get_nodes(["bad"]) == {}


def test_search_and_filters(engine):
    ids = build_project(engine)
    assert {n["id"] for n in engine.search("wizard")} == {ids["epic"], ids["initiative"]}
    assert {n["id"] for n in engine.search("WIZARD", class_name="WorkItem")} == {ids["epic"], ids["initiative"]}
    assert [n["id"] for n in engine.recall("WorkItem", {"part_of": ids["epic"]})] == [ids["task"]]
    assert [n["id"] for n in engine.recall("Goal", {"level": "key_result"})] == [ids["kr"]]
    assert [n["id"] for n in engine.recall("Node", {"created_by": "agent-it"}, limit=2)] == [ids["task"], ids["epic"]]


def test_archived_hidden_unless_requested(engine):
    ids = build_project(engine)
    engine.remember("WorkItem", {"id": ids["task"], "status": "archived"})
    assert ids["task"] not in {n["id"] for n in engine.recall("WorkItem")}
    assert ids["task"] in {n["id"] for n in engine.recall("WorkItem", include_archived=True)}


# ------------------------------------------------------------ Ladybug specifics


def test_validate_populated_graph(engine):
    build_project(engine)
    result = engine.validate()
    assert result["valid"] and result["checked"] == {"nodes": 6, "edges": 6}


def test_get_neighbourhood_and_unlink(engine):
    ids = build_project(engine)
    engine.remember("WorkItem", {"id": "t2", "title": "T2", "summary": "s", "kind": "task", "part_of": ids["epic"],
                                 "depends_on": [ids["task"]]})
    near = engine.get(ids["task"], depth=1)
    assert {n["id"] for n in near["neighbours"]} == {ids["epic"], "t2"}
    engine.unlink("t2", "depends_on", ids["task"])
    assert engine.db.edges(from_ids=["t2"], rel="DEPENDS_ON") == []
    assert engine.db.remove_edge("t2", "DEPENDS_ON", ids["task"]) is False


def test_none_clears_property(engine):
    ids = build_project(engine)
    engine.remember("Goal", {"id": ids["objective"], "due": "2027-01-01"})
    node = engine.remember("Goal", {"id": ids["objective"], "due": None})["node"]
    assert "due" not in node


def test_class_change_rejected(engine, driver):
    engine.remember("Project", {"id": "p", "title": "P", "summary": "s"})
    with pytest.raises(ValidationError, match="is a Project"):
        driver.write_node("p", "Actor", {"title": "x"}, {}, {})


def test_failed_edge_rolls_back_node_write(engine, driver):
    engine.remember("Project", {"id": "p", "title": "P", "summary": "s"})
    with pytest.raises(NotFoundError):
        driver.write_node("g", "Goal", {"title": "G", "summary": "s", "level": "objective"}, {},
                          {"PART_OF": [("missing", {})]})
    assert driver.get_nodes(["g"]) == {}


def test_persists_across_reopen(ontology, db_path):
    first = LadybugDriver(db_path, ontology.identifier)
    OntologyEngine(ontology, first).init()
    ids = build_project(OntologyEngine(ontology, first))
    first.close()
    second = LadybugDriver(db_path, ontology.identifier)
    try:
        ctx = OntologyEngine(ontology, second).context(ids["task"])
        assert [a["id"] for a in ctx["ancestors"]] == [ids["epic"], ids["initiative"], ids["project"]]
    finally:
        second.close()


def test_schema_is_idempotent(ontology, driver):
    schema = schema_for(ontology)
    driver.ensure_schema(schema)
    before = (driver._columns, driver._rels)
    driver.ensure_schema(schema)
    assert (driver._columns, driver._rels) == before


def test_additive_migration(db_path):
    v1 = parse_ontology({
        "id": "t", "name": "t",
        "slots": {"id": {"identifier": True}, "title": {},
                  "part_of": {"range": "A", "annotations": {"edge": True}}},
        "classes": {"A": {"slots": ["id", "title", "part_of"]}},
    })
    v2 = parse_ontology({
        "id": "t", "name": "t",
        "slots": {"id": {"identifier": True}, "title": {}, "score": {"range": "integer"},
                  "part_of": {"any_of": [{"range": "A"}, {"range": "B"}], "annotations": {"edge": True}}},
        "classes": {"A": {"slots": ["id", "title", "score", "part_of"]}, "B": {"slots": ["id", "title"]}},
    })
    drv = LadybugDriver(db_path)
    drv.ensure_schema(schema_for(v1))
    drv.write_node("a1", "A", {"title": "old"}, {}, {})
    drv.close()

    drv = LadybugDriver(db_path)
    try:
        drv.ensure_schema(schema_for(v2))
        assert drv.get_nodes(["a1"])["a1"].props["title"] == "old"
        drv.write_node("b1", "B", {"title": "b"}, {}, {})
        drv.write_node("a1", "A", {"score": 3}, {}, {"PART_OF": [("b1", {})]})
        assert drv.get_nodes(["a1"])["a1"].props["score"] == 3
        assert [(e.src, e.dst) for e in drv.edges(rel="PART_OF")] == [("a1", "b1")]
    finally:
        drv.close()


def test_incompatible_column_type_is_reported(db_path):
    def onto(rng):
        return parse_ontology({"id": "t", "name": "t", "slots": {"id": {"identifier": True}, "n": {"range": rng}},
                               "classes": {"A": {"slots": ["id", "n"]}}})

    drv = LadybugDriver(db_path)
    try:
        drv.ensure_schema(schema_for(onto("string")))
        with pytest.raises(DatabaseError, match="needs a rebuild"):
            drv.ensure_schema(schema_for(onto("integer")))
    finally:
        drv.close()


def test_open_driver_creates_schema(ontology, db_path):
    drv = open_driver(Settings.from_env(db_path=db_path), ontology)
    try:
        assert set(schema_for(ontology).labels) <= set(drv._columns)
        OntologyEngine(ontology, drv).remember("Project", {"id": "p", "title": "P", "summary": "s"})
    finally:
        drv.close()


def test_locked_by_another_process(ontology, db_path):
    """A second process cannot open the file while this one holds it; it waits, then reports busy."""
    holder = LadybugDriver(db_path, ontology.identifier)
    try:
        script = textwrap.dedent(f"""
            import json, sys
            from memory_engine.core.errors import DatabaseBusyError
            from memory_engine.database.ladybug_driver import LadybugDriver
            try:
                LadybugDriver({str(db_path)!r}, lock_timeout=0.3)
            except DatabaseBusyError as exc:
                print(json.dumps({{"code": exc.code}}))
        """)
        out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
        assert '"database_busy"' in out.stdout, out.stderr
    finally:
        holder.close()
    LadybugDriver(db_path, ontology.identifier).close()  # released on close

