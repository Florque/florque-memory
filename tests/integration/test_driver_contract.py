"""The GraphDriver contract, run against every backend so they stay interchangeable:
the in-memory fake, embedded Ladybug (always) and Neo4j (skipped when unreachable)."""

import uuid

import pytest
from fakes import InMemoryDriver

from memory_engine.config import Settings
from memory_engine.core.errors import NotFoundError
from memory_engine.core.schema import schema_for
from memory_engine.database.ladybug_driver import LadybugDriver


@pytest.fixture(params=["memory", "ladybug", "neo4j"])
def db(request, ontology, tmp_path):
    """(driver, id prefix). Neo4j shares one database, so its nodes get a unique prefix and are removed after."""
    prefix = ""
    if request.param == "memory":
        drv = InMemoryDriver(ontology.identifier)
    elif request.param == "ladybug":
        drv = LadybugDriver(tmp_path / "contract.lbdb", ontology.identifier)
    else:
        neo4j_driver = pytest.importorskip("memory_engine.database.neo4j_driver")
        s = Settings.from_env()
        drv = neo4j_driver.Neo4jDynamicDriver(
            s.neo4j_uri, (s.neo4j_user, s.neo4j_password), s.neo4j_database, ontology.identifier,
            connection_timeout=2,
        )
        try:
            drv.verify()
        except Exception as exc:  # noqa: BLE001 - any failure means "no Neo4j here"
            drv.close()
            pytest.skip(f"Neo4j not reachable at {s.neo4j_uri}: {exc}")
        prefix = f"ct-{uuid.uuid4().hex[:8]}-"
    drv.ensure_schema(schema_for(ontology))
    yield drv, prefix
    if request.param == "neo4j":
        with drv._session() as session:
            session.run("MATCH (n:Node) WHERE n.id STARTS WITH $p DETACH DELETE n", p=prefix).consume()
    drv.close()


def seed(drv, p):
    """project <- objective <- kr ; project <- task -SERVES-> kr"""
    drv.write_node(f"{p}project", "Project", {"title": "Florque", "summary": "Flower shop.", "status": "active"}, {}, {})
    drv.write_node(f"{p}objective", "Goal", {"title": "Grow", "summary": "More users.", "level": "objective",
                                             "status": "active"}, {}, {"PART_OF": [(f"{p}project", {})]})
    drv.write_node(f"{p}kr", "Goal", {"title": "Activation", "summary": "40% activate.", "level": "key_result",
                                      "status": "archived"}, {}, {"PART_OF": [(f"{p}objective", {})]})
    drv.write_node(f"{p}task", "WorkItem", {"title": "Build Wizard", "summary": "First step.", "kind": "task",
                                            "status": "active"}, {"created_by": "agent"},
                   {"PART_OF": [(f"{p}project", {})], "SERVES": [(f"{p}kr", {})]})


def ids(nodes, p):
    return [n.id for n in nodes if n.id.startswith(p)]


def test_write_and_get(db):
    drv, p = db
    seed(drv, p)
    nodes = drv.get_nodes([f"{p}task", f"{p}project", f"{p}missing"])
    assert set(nodes) == {f"{p}task", f"{p}project"}
    task = nodes[f"{p}task"]
    assert task.cls == "WorkItem"
    assert task.props["id"] == f"{p}task" and task.props["title"] == "Build Wizard"
    assert task.props["created_by"] == "agent"
    assert task.props["created_at"] <= task.props["updated_at"]
    assert drv.get_nodes([]) == {}


def test_update_props(db):
    drv, p = db
    seed(drv, p)
    created = drv.get_nodes([f"{p}task"])[f"{p}task"].props["created_at"]
    drv.write_node(f"{p}task", "WorkItem", {"title": "Renamed", "due": None}, {"created_by": "other"}, {})
    drv.write_node(f"{p}task", "WorkItem", {"acceptance_criteria": "Works"}, {}, {})
    drv.write_node(f"{p}task", "WorkItem", {"acceptance_criteria": None}, {}, {})
    props = drv.get_nodes([f"{p}task"])[f"{p}task"].props
    assert props["title"] == "Renamed" and props["summary"] == "First step."
    assert props["created_by"] == "agent"  # create_props only apply on create
    assert "acceptance_criteria" not in props and "due" not in props
    assert props["created_at"] == created and props["updated_at"] > created


def test_edges_queries(db):
    drv, p = db
    seed(drv, p)
    out = drv.edges(from_ids=[f"{p}task"])
    assert sorted((e.rel, e.dst) for e in out) == [("PART_OF", f"{p}project"), ("SERVES", f"{p}kr")]
    assert [e.src for e in drv.edges(to_ids=[f"{p}project"], rel="PART_OF")] in (
        [f"{p}objective", f"{p}task"], [f"{p}task", f"{p}objective"],
    )
    assert [e.dst for e in drv.edges(from_ids=[f"{p}task"], to_ids=[f"{p}kr"])] == [f"{p}kr"]
    assert drv.edges(from_ids=[f"{p}task"], rel="DEPENDS_ON") == []
    assert {"created_at", "updated_at"} <= set(out[0].props)


def test_replace_edges(db):
    drv, p = db
    seed(drv, p)
    drv.write_node(f"{p}t2", "WorkItem", {"title": "t2", "summary": "s", "kind": "task"}, {},
                   {"PART_OF": [(f"{p}project", {})]})
    drv.write_node(f"{p}t3", "WorkItem", {"title": "t3", "summary": "s", "kind": "task"}, {},
                   {"PART_OF": [(f"{p}project", {})], "DEPENDS_ON": [(f"{p}task", {}), (f"{p}t2", {})]})
    drv.write_node(f"{p}t3", "WorkItem", {}, {}, {"DEPENDS_ON": [(f"{p}t2", {})]})
    assert [e.dst for e in drv.edges(from_ids=[f"{p}t3"], rel="DEPENDS_ON")] == [f"{p}t2"]
    drv.write_node(f"{p}t3", "WorkItem", {}, {}, {"DEPENDS_ON": []})
    assert drv.edges(from_ids=[f"{p}t3"], rel="DEPENDS_ON") == []
    assert len(drv.edges(from_ids=[f"{p}t3"], rel="PART_OF")) == 1  # untouched slot kept


def test_add_and_remove_edge(db):
    drv, p = db
    seed(drv, p)
    drv.write_node(f"{p}alex", "Actor", {"title": "Alex", "summary": "s", "kind": "person"}, {}, {})
    drv.add_edge(f"{p}alex", "MEMBER_OF", f"{p}project", {"role": "viewer"}, single=False)
    drv.add_edge(f"{p}alex", "MEMBER_OF", f"{p}project", {"role": "admin"}, single=False)
    [edge] = drv.edges(from_ids=[f"{p}alex"], rel="MEMBER_OF")
    assert edge.props["role"] == "admin"

    drv.write_node(f"{p}team", "Actor", {"title": "Team", "summary": "s", "kind": "team"}, {}, {})
    drv.write_node(f"{p}team2", "Actor", {"title": "Team 2", "summary": "s", "kind": "team"}, {}, {})
    drv.add_edge(f"{p}alex", "PART_OF", f"{p}team", {}, single=True)
    drv.add_edge(f"{p}alex", "PART_OF", f"{p}team2", {}, single=True)
    assert [e.dst for e in drv.edges(from_ids=[f"{p}alex"], rel="PART_OF")] == [f"{p}team2"]

    assert drv.remove_edge(f"{p}alex", "PART_OF", f"{p}team2") is True
    assert drv.remove_edge(f"{p}alex", "PART_OF", f"{p}team2") is False
    with pytest.raises(NotFoundError):
        drv.add_edge(f"{p}alex", "PART_OF", f"{p}nobody", {}, single=True)


def test_find_nodes(db):
    drv, p = db
    seed(drv, p)
    assert ids(drv.find_nodes(labels=["Goal"]), p) == [f"{p}kr", f"{p}objective"]  # most recent first
    assert ids(drv.find_nodes(labels=["Goal"], filters={"level": "objective"}), p) == [f"{p}objective"]
    assert ids(drv.find_nodes(labels=["Goal", "WorkItem"], filters={"id": f"{p}task"}), p) == [f"{p}task"]
    assert ids(drv.find_nodes(filters={"created_by": "agent", "id": f"{p}task"}), p) == [f"{p}task"]
    assert ids(drv.find_nodes(labels=["Goal"], exclude={"status": "archived"}), p) == [f"{p}objective"]
    assert ids(drv.find_nodes(edge_filters={"PART_OF": f"{p}project"}), p) == [f"{p}task", f"{p}objective"]
    assert ids(drv.find_nodes(edge_filters={"SERVES": f"{p}kr"}), p) == [f"{p}task"]
    assert ids(drv.find_nodes(labels=["Project"], edge_filters={"SERVES": f"{p}kr"}), p) == []
    assert ids(drv.find_nodes(labels=["Goal"], filters={"uri": "x"}), p) == []  # Goal has no `uri`
    found = drv.find_nodes(text="WIZARD", text_keys=["title", "summary", "uri"])
    assert ids(found, p) == [f"{p}task"]
    assert ids(drv.find_nodes(text="activ", text_keys=["title"], labels=["Goal", "Project"]), p) == [f"{p}kr"]
    assert len(ids(drv.find_nodes(labels=["Goal", "WorkItem", "Project"], filters={"status": "active"}), p)) == 3
    if not p:  # limit is only exact on a database holding just this test's nodes
        assert ids(drv.find_nodes(limit=2), p) == [f"{p}task", f"{p}kr"]
