"""Database identity stamp on real Ladybug files: a database is only opened by the ontology or project
it was created for. A refused open must leave the file untouched."""

import pytest

from memory_engine.config import Settings, bundled_ontologies
from memory_engine.core.engine import OntologyEngine
from memory_engine.core.errors import OntologyMismatchError, ValidationError
from memory_engine.core.identity import identity_of
from memory_engine.core.parser import load_ontology
from memory_engine.core.project import load_project
from memory_engine.core.schema import schema_for
from memory_engine.database import open_driver
from memory_engine.database.ladybug_driver import LadybugDriver

pytestmark = pytest.mark.ladybug


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("MEMORY_PROJECT", "MEMORY_ONTOLOGY", "MEMORY_DB_PATH"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MEMORY_BACKEND", "ladybug")


def manifest(tmp_path, name, domain, prefix=None, work="workgraph"):
    path = tmp_path / f"{name}.yaml"
    text = f"name: {name}\nwork: {work}\ndomain: {bundled_ontologies()[domain]}\n"
    if prefix:
        text += f"domain_prefix: {prefix}\n"
    path.write_text(text, encoding="utf-8")
    return path


def open_project(db, manifest_path):
    settings = Settings.from_env(project=manifest_path, db_path=str(db))
    return open_driver(settings, load_project(manifest_path))


def open_standalone(db, ontology_name):
    settings = Settings.from_env(ontology_path=bundled_ontologies()[ontology_name], db_path=str(db))
    return open_driver(settings, load_ontology(bundled_ontologies()[ontology_name]))


def table_names(db, ontology):
    driver = LadybugDriver(str(db), ontology.identifier)
    try:
        return set(driver._columns) | set(driver._rels) | set(driver._rel_columns)
    finally:
        driver.close()


def test_new_database_is_stamped_and_reopens(tmp_path):
    db = tmp_path / "p.lbdb"
    m = manifest(tmp_path, "payments", "architecture")
    driver = open_project(db, m)
    try:
        stamp = driver.read_meta("payments")
    finally:
        driver.close()
    assert stamp["kind"] == "project"
    assert stamp["domain"] == "architecture" and stamp["domain_prefix"] == "dom"

    driver = open_project(db, m)  # same identity: opens normally
    driver.close()


def test_grown_ontology_still_migrates(tmp_path):
    db = tmp_path / "w.lbdb"
    ont = tmp_path / "grow.yaml"
    ont.write_text(
        "id: https://x/g\nname: grow\ndefault_range: string\n"
        "slots:\n  id: {identifier: true}\n  name: {}\n"
        "classes:\n  Task: {slots: [id, name]}\n",
        encoding="utf-8",
    )
    driver = open_driver(Settings.from_env(ontology_path=ont, db_path=str(db)), load_ontology(ont))
    driver.close()

    ont.write_text(
        "id: https://x/g\nname: grow\ndefault_range: string\n"
        "slots:\n  id: {identifier: true}\n  name: {}\n"
        "classes:\n  Task: {slots: [id, name]}\n  Note: {slots: [id, name]}\n",
        encoding="utf-8",
    )
    driver = open_driver(Settings.from_env(ontology_path=ont, db_path=str(db)), load_ontology(ont))
    try:
        assert "Note" in driver._columns
    finally:
        driver.close()


def test_changed_domain_is_refused_and_file_is_untouched(tmp_path):
    db = tmp_path / "p.lbdb"
    open_project(db, manifest(tmp_path, "payments", "architecture")).close()
    before = table_names(db, load_ontology(bundled_ontologies()["architecture"]))

    other = manifest(tmp_path, "payments", "family_tree")
    with pytest.raises(OntologyMismatchError) as exc:
        open_project(db, other)
    assert exc.value.code == "ontology_mismatch"
    assert any("different" in issue or "created for" in issue for issue in exc.value.issues)
    assert table_names(db, load_ontology(bundled_ontologies()["architecture"])) == before


def test_changed_prefix_is_refused(tmp_path):
    db = tmp_path / "p.lbdb"
    open_project(db, manifest(tmp_path, "payments", "architecture")).close()
    with pytest.raises(OntologyMismatchError):
        open_project(db, manifest(tmp_path, "payments", "architecture", prefix="area"))


def test_project_named_like_a_standalone_ontology_is_refused(tmp_path):
    db = tmp_path / "shared.lbdb"
    open_standalone(db, "workgraph").close()
    with pytest.raises(OntologyMismatchError):
        open_project(db, manifest(tmp_path, "workgraph", "architecture"))


def test_database_created_before_stamps_is_adopted(tmp_path):
    db = tmp_path / "legacy.lbdb"
    ontology = load_ontology(bundled_ontologies()["workgraph"])
    raw = LadybugDriver(str(db), ontology.identifier)
    raw.ensure_schema(schema_for(ontology))
    raw.close()  # no stamp written: how databases looked before this feature

    driver = open_standalone(db, "workgraph")
    try:
        assert driver.read_meta("workgraph") == identity_of(ontology)
    finally:
        driver.close()


def test_reserved_identity_id_is_rejected_and_hidden(tmp_path):
    db = tmp_path / "p.lbdb"
    driver = open_project(db, manifest(tmp_path, "payments", "architecture"))
    engine = OntologyEngine(load_project(manifest(tmp_path, "payments", "architecture")), driver)
    try:
        with pytest.raises(ValidationError, match="reserved"):
            engine.remember("Project", {"id": "__meta__", "title": "x", "summary": "y"})
        assert "__meta__" not in {n.id for n in driver.find_nodes(labels=None)}
        assert engine.validate()["valid"]
    finally:
        driver.close()
