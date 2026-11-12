import pytest
from conftest import build_project
from fakes import InMemoryDriver

from memory_engine.core.engine import OntologyEngine
from memory_engine.core.errors import NotFoundError, ValidationError


@pytest.fixture
def db():
    return InMemoryDriver()


@pytest.fixture
def engine(ontology, db):
    return OntologyEngine(ontology, db, actor="agent-claude")


@pytest.fixture
def ids(engine):
    return build_project(engine)


def rejected(fn, *fragments):
    with pytest.raises(ValidationError) as info:
        fn()
    text = str(info.value)
    for fragment in fragments:
        assert fragment in text, text
    return info.value


def test_create_sets_defaults_and_attribution(engine, db):
    result = engine.remember("Project", {"title": "Florque", "summary": "The product."})
    node = result["node"]
    assert result["created"]
    assert node["id"].startswith("project-")
    assert node["status"] == "proposed"
    assert node["created_by"] == node["updated_by"] == "agent-claude"
    assert node["created_at"] and node["updated_at"]


def test_upsert_does_not_duplicate(engine, db, ids):
    result = engine.remember("Project", {"id": ids["project"], "summary": "Updated summary.", "status": "active"})
    assert not result["created"]
    assert len([n for n in db.nodes.values() if n.cls == "Project"]) == 1
    node = result["node"]
    assert node["title"] == "Florque" and node["summary"] == "Updated summary." and node["status"] == "active"


def test_class_cannot_change(engine, ids):
    rejected(lambda: engine.remember("Goal", {"id": ids["project"]}), "is a Project, not a Goal")


def test_required_fields(engine):
    rejected(lambda: engine.remember("Project", {"title": "No summary"}), "`summary` is required")


def test_required_field_cannot_be_cleared(engine, ids):
    rejected(lambda: engine.remember("Project", {"id": ids["project"], "summary": None}), "`summary` is required")


def test_unknown_and_system_fields(engine):
    rejected(
        lambda: engine.remember("Project", {"title": "x", "summary": "y", "owner": "bob", "created_at": "2020-01-01"}),
        "`owner` is not a slot of Project",
        "`created_at` is managed by the engine",
    )


def test_enum_errors(engine, ids):
    rejected(
        lambda: engine.remember("Goal", {"title": "x", "summary": "y", "level": "milestone", "part_of": ids["project"]}),
        "`milestone` is not a valid GoalLevel",
    )


def test_type_casting(engine, ids):
    node = engine.remember("Goal", {"id": ids["kr"], "due": "2027-03-31"})["node"]
    assert str(node["due"]) == "2027-03-31"
    rejected(lambda: engine.remember("Goal", {"id": ids["kr"], "due": "next spring"}), "`due`")


def test_key_result_needs_target(engine, ids):
    rejected(
        lambda: engine.remember("Goal", {
            "title": "KR", "summary": "s", "level": "key_result", "part_of": ids["objective"],
        }),
        "`target` is required when level=key_result",
    )


def test_initiative_must_serve_a_goal(engine, ids):
    rejected(
        lambda: engine.remember("WorkItem", {
            "title": "I", "summary": "s", "kind": "initiative", "part_of": ids["project"],
        }),
        "`serves` is required",
    )


def test_part_of_required(engine):
    rejected(lambda: engine.remember("WorkItem", {"title": "t", "summary": "s", "kind": "task"}), "`part_of` is required")


def test_allowed_parent(engine, ids):
    # a task may not sit directly under the Project
    rejected(
        lambda: engine.remember("WorkItem", {"title": "t", "summary": "s", "kind": "task", "part_of": ids["project"]}),
        "`part_of` cannot point to `project` (Project); allowed: WorkItem:epic, WorkItem:initiative",
    )
    # a key result must sit under an objective, not under another key result
    rejected(
        lambda: engine.remember("Goal", {
            "title": "k", "summary": "s", "level": "key_result", "target": "t", "part_of": ids["kr"],
        }),
        "allowed: Goal:objective",
    )


def test_edge_range(engine, ids):
    rejected(
        lambda: engine.remember("WorkItem", {"id": ids["task"], "serves": [ids["epic"]]}),
        "`serves` cannot point to `epic` (WorkItem:epic); allowed: Goal",
    )


def test_missing_target(engine, ids):
    rejected(lambda: engine.remember("WorkItem", {"id": ids["task"], "depends_on": ["ghost"]}), "`ghost` does not exist")


def test_single_valued_edge_takes_one_target(engine, ids):
    rejected(
        lambda: engine.remember("WorkItem", {"id": ids["task"], "part_of": [ids["epic"], ids["initiative"]]}),
        "`part_of` takes one target",
    )


def test_payload_edges_replace_but_omitted_edges_are_kept(engine, db, ids):
    engine.remember("WorkItem", {"id": ids["task"], "part_of": ids["initiative"]})
    assert [e.dst for e in db.edges(from_ids=[ids["task"]], rel="PART_OF")] == [ids["initiative"]]
    engine.remember("WorkItem", {"id": ids["task"], "summary": "Changed."})
    assert [e.dst for e in db.edges(from_ids=[ids["task"]], rel="PART_OF")] == [ids["initiative"]]


def test_link_replaces_single_valued_edge(engine, db, ids):
    result = engine.link(ids["task"], "part_of", ids["initiative"])
    assert result["replaced"] == [ids["epic"]]
    assert [e.dst for e in db.edges(from_ids=[ids["task"]], rel="PART_OF")] == [ids["initiative"]]


def test_link_adds_to_multivalued_edge(engine, db, ids):
    other = engine.remember("WorkItem", {"title": "t2", "summary": "s", "kind": "task", "part_of": ids["epic"]})
    engine.link(ids["task"], "depends_on", other["node"]["id"])
    engine.link(ids["task"], "DEPENDS_ON", ids["epic"])
    assert {e.dst for e in db.edges(from_ids=[ids["task"]], rel="DEPENDS_ON")} == {other["node"]["id"], ids["epic"]}


def test_containment_cycle_rejected(engine, ids):
    rejected(lambda: engine.link(ids["epic"], "part_of", ids["epic"]), "containment cycle")


def test_containment_cycle_through_descendant(ontology, db):
    engine = OntologyEngine(ontology, db)
    engine.remember("Actor", {"id": "team", "title": "Team", "summary": "s", "kind": "team"})
    engine.remember("Actor", {"id": "squad", "title": "Squad", "summary": "s", "kind": "team", "part_of": "team"})
    engine.remember("Actor", {"id": "alex", "title": "Alex", "summary": "s", "kind": "person", "part_of": "squad"})
    rejected(lambda: engine.link("team", "part_of", "alex"), "would create a containment cycle")


def test_edge_properties_required_and_cast(engine, ids):
    engine.remember("Actor", {"id": "alex", "title": "Alex", "summary": "Engineer.", "kind": "person"})
    rejected(lambda: engine.link("alex", "member_of", ids["project"]), "needs edge property `role`")
    rejected(lambda: engine.link("alex", "member_of", ids["project"], {"role": "owner"}), "not a valid Role")
    rejected(lambda: engine.link("alex", "member_of", ids["project"], {"level": "x"}), "no edge property `level`")
    result = engine.link("alex", "member_of", ids["project"], {"role": "Admin"})
    assert result["edge"]["role"] == "admin"
    member = engine.remember("Actor", {
        "id": "sam", "title": "Sam", "summary": "s", "kind": "person",
        "member_of": [{"id": ids["project"], "role": "viewer"}],
    })
    assert member["created"]


def test_unlink_refuses_required_edge(engine, ids):
    rejected(lambda: engine.unlink(ids["initiative"], "serves", ids["kr"]), "`serves` is required")
    with pytest.raises(NotFoundError):
        engine.unlink(ids["task"], "depends_on", ids["epic"])
    engine.link(ids["task"], "depends_on", ids["epic"])
    assert engine.unlink(ids["task"], "depends_on", ids["epic"])["removed"]["to"] == ids["epic"]


def test_unknown_edge_slot(engine, ids):
    rejected(lambda: engine.link(ids["task"], "blocks", ids["epic"]), "WorkItem has no edge `blocks`")
    rejected(lambda: engine.link(ids["project"], "part_of", ids["epic"]), "Project has no edge `part_of`")


def test_recall_filters(engine, ids):
    tasks = engine.recall("WorkItem", {"kind": "task"})
    assert [t["id"] for t in tasks] == [ids["task"]]
    under_epic = engine.recall("WorkItem", {"part_of": ids["epic"]})
    assert [t["id"] for t in under_epic] == [ids["task"]]
    assert {n["id"] for n in engine.recall("Node")} == set(ids.values())
    rejected(lambda: engine.recall("WorkItem", {"colour": "red"}), "cannot filter WorkItem by `colour`")


def test_archived_hidden_by_default(engine, ids):
    engine.remember("WorkItem", {"id": ids["task"], "status": "archived"})
    assert engine.recall("WorkItem", {"kind": "task"}) == []
    assert len(engine.recall("WorkItem", {"kind": "task"}, include_archived=True)) == 1
    assert engine.context(ids["epic"])["children"] == []


def test_search(engine, ids):
    assert {n["id"] for n in engine.search("WIZARD")} == {ids["epic"], ids["initiative"]}
    assert [n["id"] for n in engine.search("activation", "Goal")] == [ids["kr"]]


def test_get_neighbourhood(engine, ids):
    result = engine.get(ids["epic"], depth=1)
    assert {n["id"] for n in result["neighbours"]} == {ids["initiative"], ids["task"]}
    assert {(e["from"], e["slot"], e["to"]) for e in result["edges"]} == {
        (ids["epic"], "part_of", ids["initiative"]),
        (ids["task"], "part_of", ids["epic"]),
    }


def test_context(engine, ids):
    engine.remember("Document", {
        "id": "spec", "title": "Wizard spec", "summary": "What the wizard does.", "kind": "spec",
        "uri": "docs/wizard.md", "part_of": ids["project"], "about": [ids["initiative"]],
    })
    engine.remember("Actor", {"id": "alex", "title": "Alex", "summary": "Engineer.", "kind": "person"})
    engine.link(ids["epic"], "owned_by", "alex")

    ctx = engine.context(ids["task"])
    assert ctx["node"]["id"] == ids["task"]
    assert [a["id"] for a in ctx["ancestors"]] == [ids["epic"], ids["initiative"], ids["project"]]
    by_slot = {(link["direction"], link["slot"]): link for link in ctx["links"]}
    serves = by_slot[("out", "serves")]
    assert serves["node"]["id"] == ids["kr"] and serves["via"] == ids["initiative"]
    assert [p["id"] for p in serves["path"]] == [ids["objective"], ids["project"]]
    assert by_slot[("out", "owned_by")]["via"] == ids["epic"]
    about = by_slot[("in", "about")]
    assert about["node"]["id"] == "spec" and about["via"] == ids["initiative"]


def test_validate_clean_graph(engine, ids):
    report = engine.validate()
    assert report["valid"], report["issues"]
    assert report["checked"] == {"nodes": 6, "edges": 6}


def test_validate_finds_corruption(engine, db, ids):
    db.rels.pop((ids["initiative"], "SERVES", ids["kr"]))  # written behind the engine's back
    db.nodes[ids["task"]].props["kind"] = "saga"
    db.add_edge(ids["epic"], "PART_OF", ids["task"], {}, single=True)  # epic <-> task loop
    db.add_edge(ids["task"], "LIKES", ids["epic"], {}, single=False)
    issues = [i["issue"] for i in engine.validate()["issues"]]
    assert any("`serves` is required" in i for i in issues)
    assert any("`saga` is not a valid WorkKind" in i for i in issues)
    assert any(i.startswith("containment cycle") for i in issues)
    assert any("LIKES" in i for i in issues)


def test_preexisting_issues_do_not_block_unrelated_updates(engine, db, ids):
    db.rels.pop((ids["initiative"], "SERVES", ids["kr"]))
    engine.remember("WorkItem", {"id": ids["initiative"], "summary": "Edited despite missing serves."})
