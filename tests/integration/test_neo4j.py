"""Engine against a real Neo4j. Skipped unless one is reachable (`docker compose up -d`)."""

import uuid

import pytest
from conftest import build_project

from memory_engine.config import Settings
from memory_engine.core.engine import OntologyEngine
from memory_engine.core.errors import ValidationError
from memory_engine.database.neo4j_driver import Neo4jDynamicDriver

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def driver(ontology):
    s = Settings.from_env()
    drv = Neo4jDynamicDriver(s.neo4j_uri, (s.neo4j_user, s.neo4j_password), s.neo4j_database, ontology.identifier)
    try:
        drv.verify()
    except Exception as exc:  # noqa: BLE001 - any connection failure means "no database here"
        drv.close()
        pytest.skip(f"Neo4j not reachable at {s.neo4j_uri}: {exc}")
    yield drv
    drv.close()


@pytest.fixture
def prefix(driver):
    """Unique id prefix per test; the test's nodes are deleted afterwards."""
    value = f"it-{uuid.uuid4().hex[:8]}-"
    yield value
    with driver._session() as session:
        session.run("MATCH (n:Node) WHERE n.id STARTS WITH $p DETACH DELETE n", p=value).consume()


@pytest.fixture
def engine(ontology, driver):
    eng = OntologyEngine(ontology, driver, actor="agent-it")
    eng.init()
    return eng


def count(driver, query, **params):
    with driver._session() as session:
        return session.run(query, **params).single()[0]


def test_upsert_is_idempotent(engine, driver, prefix):
    pid = f"{prefix}project"
    for summary in ("First.", "Second."):
        engine.remember("Project", {"id": pid, "title": "P", "summary": summary})
    assert count(driver, "MATCH (n:Node {id: $id}) RETURN count(n)", id=pid) == 1
    node = engine.recall("Project", {"id": pid})[0]
    assert node["summary"] == "Second."
    assert node["created_by"] == "agent-it"
    assert node["created_at"] < node["updated_at"]
    assert count(driver, "MATCH (n:Project:Node {id: $id}) RETURN count(n)", id=pid) == 1


def test_uniqueness_constraint_exists(engine, driver):
    names = [r["name"] for r in driver._session().run("SHOW CONSTRAINTS YIELD name")]
    assert "node_id_unique" in names


def test_tree_edges_and_context(engine, driver, prefix):
    ids = build_project(engine, prefix)
    assert count(
        driver, "MATCH (:Node {id: $t})-[:PART_OF*]->(p:Project) RETURN count(p)", t=ids["task"]
    ) == 1
    ctx = engine.context(ids["task"])
    assert [a["id"] for a in ctx["ancestors"]] == [ids["epic"], ids["initiative"], ids["project"]]
    serves = [link for link in ctx["links"] if link["slot"] == "serves"]
    assert serves and serves[0]["node"]["id"] == ids["kr"]
    assert str(engine.remember("Goal", {"id": ids["kr"], "due": "2027-03-31"})["node"]["due"]) == "2027-03-31"


def test_replace_and_link_semantics(engine, driver, prefix):
    ids = build_project(engine, prefix)
    engine.link(ids["task"], "part_of", ids["initiative"])
    parents = count(driver, "MATCH (:Node {id: $t})-[r:PART_OF]->() RETURN count(r)", t=ids["task"])
    assert parents == 1
    engine.remember("Actor", {
        "id": f"{prefix}alex", "title": "Alex", "summary": "s", "kind": "person",
        "member_of": [{"id": ids["project"], "role": "admin"}],
    })
    role = count(driver, "MATCH (:Node {id: $a})-[r:MEMBER_OF]->() RETURN r.role", a=f"{prefix}alex")
    assert role == "admin"


def test_rejected_write_leaves_graph_untouched(engine, driver, prefix):
    ids = build_project(engine, prefix)
    with pytest.raises(ValidationError):
        engine.remember("WorkItem", {"id": f"{prefix}bad", "title": "t", "summary": "s", "kind": "task", "part_of": ids["project"]})
    assert count(driver, "MATCH (n:Node {id: $id}) RETURN count(n)", id=f"{prefix}bad") == 0


def test_search_and_filters(engine, prefix):
    ids = build_project(engine, prefix)
    found = {n["id"] for n in engine.search("wizard") if n["id"].startswith(prefix)}
    assert found == {ids["epic"], ids["initiative"]}
    under_epic = engine.recall("WorkItem", {"part_of": ids["epic"]})
    assert [n["id"] for n in under_epic] == [ids["task"]]
