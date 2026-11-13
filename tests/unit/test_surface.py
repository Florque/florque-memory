"""CLI, tool dispatch and the generated agent guide."""

import json
from pathlib import Path

from click.testing import CliRunner
from fakes import InMemoryDriver

from memory_engine.cli import main
from memory_engine.core.engine import OntologyEngine
from memory_engine.core.guide import describe_json
from memory_engine.tools import TOOLS, handle_agent_call


def test_describe_json(ontology):
    data = describe_json(ontology, "WorkItem")
    assert list(data["classes"]) == ["WorkItem"]
    assert data["classes"]["WorkItem"]["edges"]["part_of"]["rel_type"] == "PART_OF"


def test_handle_agent_call(ontology):
    engine = OntologyEngine(ontology, InMemoryDriver())
    ok = handle_agent_call(engine, "remember_entity", {
        "entity_type": "Project", "properties": {"id": "p", "title": "P", "summary": "S"},
    })
    assert ok["status"] == "success" and ok["node"]["id"] == "p"
    bad = handle_agent_call(engine, "remember_entity", {"entity_type": "Project", "properties": {"id": "q"}})
    assert bad["status"] == "error" and bad["error"] == "validation_error"
    assert "`title` is required" in bad["issues"]
    assert handle_agent_call(engine, "recall_context", {"entity_type": "Project"})["data"][0]["id"] == "p"
    assert handle_agent_call(engine, "nope", {})["error"] == "unknown_tool"
    assert handle_agent_call(engine, "link_entities", {"from_id": "p"})["message"] == "Missing argument `relation_type`"


def test_cli_describe_markdown():
    result = CliRunner().invoke(main, ["describe", "--class", "goal"])
    assert result.exit_code == 0
    assert result.output.startswith("# Memory ontology")
    assert "### Goal" in result.output and "### WorkItem" not in result.output


def test_cli_rejected_request_envelope():
    result = CliRunner().invoke(main, ["remember", "Ticket", "-p", "title=x"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["status"] == "error" and "Unknown class `Ticket`" in payload["message"]


def test_cli_neo4j_unreachable_envelope(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:1")
    result = CliRunner().invoke(main, ["--backend", "neo4j", "validate"])
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["error"] == "database_unavailable" and "127.0.0.1:1" in payload["message"]


def test_cli_ladybug_round_trip(tmp_path):
    db = str(tmp_path / "cli" / "graph.lbdb")  # parent dir is created on first use
    runner = CliRunner()
    created = runner.invoke(main, ["--db", db, "remember", "Project", "-p", "id=florque", "-p", "title=Florque",
                                   "-p", "summary=The product."])
    assert created.exit_code == 0, created.output
    assert json.loads(created.output)["created"] is True
    context = json.loads(runner.invoke(main, ["--db", db, "context", "florque"]).output)
    assert context["status"] == "success" and context["node"]["title"] == "Florque"
    assert Path(db).exists()


def test_cli_default_db_is_per_ontology(tmp_path):
    result = CliRunner().invoke(main, ["--ontology", "workgraph", "remember", "Project", "-p", "id=p",
                                       "-p", "title=P", "-p", "summary=s"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "data" / "workgraph.lbdb").exists()


def test_cli_unknown_ontology_name():
    result = CliRunner().invoke(main, ["--ontology", "nope", "validate"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["error"] == "ontology_error" and "bundled:" in payload["message"] and "workgraph" in payload["message"]


def test_cli_unknown_backend(monkeypatch):
    monkeypatch.setenv("MEMORY_BACKEND", "sqlite")
    result = CliRunner().invoke(main, ["validate"])
    assert result.exit_code == 2 and "Unknown MEMORY_BACKEND" in result.output


def test_cli_tools_lists_registry():
    payload = json.loads(CliRunner().invoke(main, ["tools"]).output)
    assert [t["name"] for t in payload["tools"]] == list(TOOLS)


def test_cli_db_path(tmp_path, monkeypatch):
    payload = json.loads(CliRunner().invoke(main, ["--ontology", "family_tree", "db-path"]).output)
    assert payload["path"] == str(tmp_path / "data" / "family-tree.lbdb")
    assert payload["exists"] is False and payload["ladybug_version"]
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.lbdb"))
    assert json.loads(CliRunner().invoke(main, ["db-path"]).output)["path"] == str(tmp_path / "x.lbdb")
