from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from memory_engine.config import default_ontology_path
from memory_engine.core.parser import Ontology, load_ontology


@pytest.fixture(autouse=True)
def _isolated_database(tmp_path, monkeypatch):
    """Never touch the user's real memory: default the Ladybug file to a per-test temp path."""
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "memory.lbdb"))
    monkeypatch.delenv("MEMORY_BACKEND", raising=False)


@pytest.fixture(scope="session")
def ontology() -> Ontology:
    return load_ontology(default_ontology_path())


def build_project(engine, prefix: str = "") -> dict[str, str]:
    """Project -> objective -> key_result; initiative serving the KR; epic; task. Returns ids."""
    ids = {k: f"{prefix}{k}" for k in ("project", "objective", "kr", "initiative", "epic", "task")}
    engine.remember("Project", {"id": ids["project"], "title": "Florque", "summary": "The product."})
    engine.remember("Goal", {
        "id": ids["objective"], "title": "Faster onboarding", "summary": "New users succeed quickly.",
        "level": "objective", "part_of": ids["project"],
    })
    engine.remember("Goal", {
        "id": ids["kr"], "title": "Activation 40%", "summary": "Share of users activated in week one.",
        "level": "key_result", "target": "Activation >= 40% by 2027-03-31", "part_of": ids["objective"],
    })
    engine.remember("WorkItem", {
        "id": ids["initiative"], "title": "Guided setup", "summary": "Wizard for first run.",
        "kind": "initiative", "part_of": ids["project"], "serves": [ids["kr"]],
    })
    engine.remember("WorkItem", {
        "id": ids["epic"], "title": "Setup wizard UI", "summary": "Screens of the wizard.",
        "kind": "epic", "part_of": ids["initiative"],
    })
    engine.remember("WorkItem", {
        "id": ids["task"], "title": "Build step 1", "summary": "Account details step.",
        "kind": "task", "part_of": ids["epic"],
    })
    return ids
