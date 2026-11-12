"""Runtime settings, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir

DEFAULT_ONTOLOGY = "workgraph_linkml.yaml"
APP_NAME = "memory-engine"
DB_FILE = "memory.lbdb"
BACKENDS = ("ladybug", "neo4j")
DEFAULT_BACKEND = "ladybug"


def default_db_path() -> Path:
    """Per-user data dir, shared by every project and agent of this OS user:
    macOS ~/Library/Application Support/memory-engine, Linux $XDG_DATA_HOME/memory-engine
    (~/.local/share/memory-engine), Windows %LOCALAPPDATA%\\memory-engine."""
    return Path(user_data_dir(APP_NAME, appauthor=False)) / DB_FILE


def default_ontology_path() -> Path:
    """The ontology bundled in the wheel, or the repo's `ontology/` dir in a source checkout."""
    bundled = Path(__file__).parent / "ontology" / DEFAULT_ONTOLOGY
    if bundled.exists():
        return bundled
    return Path(__file__).resolve().parents[2] / "ontology" / DEFAULT_ONTOLOGY


@dataclass(frozen=True)
class Settings:
    backend: str
    db_path: str  # Ladybug file, or ":memory:"
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    neo4j_database: str | None
    ontology_path: Path
    actor: str | None

    @classmethod
    def from_env(
        cls,
        ontology_path: str | os.PathLike | None = None,
        db_path: str | os.PathLike | None = None,
        backend: str | None = None,
    ) -> Settings:
        """Explicit arguments (CLI options) win over environment variables, which win over defaults."""
        env = os.environ
        ontology = ontology_path or env.get("MEMORY_ONTOLOGY")
        backend = (backend or env.get("MEMORY_BACKEND") or DEFAULT_BACKEND).strip().lower()
        if backend not in BACKENDS:
            raise ValueError(f"Unknown MEMORY_BACKEND {backend!r}; expected one of {', '.join(BACKENDS)}")
        return cls(
            backend=backend,
            db_path=str(db_path or env.get("MEMORY_DB_PATH") or default_db_path()),
            neo4j_uri=env.get("NEO4J_URI", "bolt://localhost:7687"),
            neo4j_user=env.get("NEO4J_USER", "neo4j"),
            neo4j_password=env.get("NEO4J_PASSWORD", "memory-engine-dev"),
            neo4j_database=env.get("NEO4J_DATABASE") or None,
            ontology_path=Path(ontology) if ontology else default_ontology_path(),
            actor=env.get("MEMORY_ACTOR") or None,
        )
