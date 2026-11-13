import pytest

from memory_engine.config import (
    Settings,
    bundled_ontologies,
    data_dir,
    default_db_path,
    default_ontology_path,
    resolve_ontology,
    slug,
)
from memory_engine.core.errors import OntologyError


def test_db_path_precedence(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "env.lbdb"))
    assert Settings.from_env().db_path == str(tmp_path / "env.lbdb")
    assert Settings.from_env(db_path=tmp_path / "cli.lbdb").db_path == str(tmp_path / "cli.lbdb")
    monkeypatch.delenv("MEMORY_DB_PATH")
    assert Settings.from_env().db_path is None  # open_driver falls back to default_db_path(ontology name)


def test_default_db_path_is_one_file_per_ontology(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORY_DATA_DIR", str(tmp_path))
    assert default_db_path("workgraph") == tmp_path / "workgraph.lbdb"
    assert default_db_path("Recipe Book") == tmp_path / "recipe-book.lbdb"


def test_data_dir_defaults_to_user_data_dir(monkeypatch):
    monkeypatch.delenv("MEMORY_DATA_DIR")
    assert data_dir().name == "memory-engine"


def test_slug():
    assert slug("Work Graph v2!") == "work-graph-v2"
    assert slug("***") == "ontology"


def test_bundled_ontologies_and_resolution(tmp_path):
    assert "workgraph" in bundled_ontologies()
    assert default_ontology_path() == bundled_ontologies()["workgraph"]
    assert default_ontology_path().parent.name == "ontologies"
    custom = tmp_path / "mine.yaml"
    custom.write_text("name: mine\n")
    assert resolve_ontology(str(custom)) == custom
    assert resolve_ontology("workgraph") == bundled_ontologies()["workgraph"]
    with pytest.raises(OntologyError, match=r"bundled: .*workgraph"):
        resolve_ontology("nope")
    assert Settings.from_env(ontology_path="workgraph").ontology_path == default_ontology_path()


def test_backend_default_and_override(monkeypatch):
    assert Settings.from_env().backend == "ladybug"
    monkeypatch.setenv("MEMORY_BACKEND", "Neo4j")
    assert Settings.from_env().backend == "neo4j"
    assert Settings.from_env(backend="ladybug").backend == "ladybug"
    monkeypatch.setenv("MEMORY_BACKEND", "sqlite")
    with pytest.raises(ValueError, match="Unknown MEMORY_BACKEND"):
        Settings.from_env()
