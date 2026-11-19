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


def test_manifest_resolves_relative_files_and_defaults_work(tmp_path):
    from memory_engine.config import DEFAULT_WORK_ONTOLOGY, load_manifest, resolve_ontology

    domain = tmp_path / "domain.yaml"
    domain.write_text("id: https://x/d\nname: d\n", encoding="utf-8")
    manifest = tmp_path / "project.yaml"
    manifest.write_text("name: payments\ndomain: domain.yaml\n", encoding="utf-8")

    loaded = load_manifest(manifest)
    assert loaded.name == "payments"
    assert loaded.domain == domain.resolve()
    assert loaded.work == resolve_ontology(DEFAULT_WORK_ONTOLOGY)


def test_manifest_rejects_unknown_keys_and_same_ontology(tmp_path):
    import pytest

    from memory_engine.config import load_manifest
    from memory_engine.core.errors import OntologyError

    bad = tmp_path / "bad.yaml"
    bad.write_text("name: p\ndomain: workgraph\nextra: 1\n", encoding="utf-8")
    with pytest.raises(OntologyError, match="unknown keys"):
        load_manifest(bad)

    same = tmp_path / "same.yaml"
    same.write_text("name: p\ndomain: workgraph\nwork: workgraph\n", encoding="utf-8")
    with pytest.raises(OntologyError, match="must be different"):
        load_manifest(same)
