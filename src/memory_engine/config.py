"""Runtime settings, read from the environment."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir

from .core.errors import OntologyError

DEFAULT_ONTOLOGY = "workgraph"
APP_NAME = "memory-engine"
DB_SUFFIX = ".lbdb"
BACKENDS = ("ladybug", "neo4j")
DEFAULT_BACKEND = "ladybug"
_ONTOLOGY_SUFFIXES = ("_linkml", ".linkml")


def data_dir() -> Path:
    """Where databases and generated skills live: $MEMORY_DATA_DIR, else the per-user data dir
    (macOS ~/Library/Application Support/memory-engine, Linux $XDG_DATA_HOME/memory-engine
    i.e. ~/.local/share/memory-engine, Windows %LOCALAPPDATA%\\memory-engine)."""
    explicit = os.environ.get("MEMORY_DATA_DIR")
    return Path(explicit).expanduser() if explicit else Path(user_data_dir(APP_NAME, appauthor=False))


def slug(name: str) -> str:
    """File- and skill-name safe form of an ontology name: `Work Graph` -> `work-graph`."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "ontology"


def default_db_path(ontology_name: str) -> Path:
    """One Ladybug file per ontology, so skills built from different ontologies never share tables."""
    return data_dir() / f"{slug(ontology_name)}{DB_SUFFIX}"


def ontologies_dir() -> Path:
    """Bundled ontologies: inside the wheel, or the repo's `ontologies/` dir in a source checkout."""
    bundled = Path(__file__).parent / "ontologies"
    if bundled.is_dir():
        return bundled
    return Path(__file__).resolve().parents[2] / "ontologies"


def _short_name(path: Path) -> str:
    stem = path.stem
    for suffix in _ONTOLOGY_SUFFIXES:
        stem = stem.removesuffix(suffix)
    return stem


def bundled_ontologies() -> dict[str, Path]:
    """Predefined ontologies by short name (`workgraph_linkml.yaml` -> `workgraph`)."""
    root = ontologies_dir()
    files = sorted([*root.glob("*.yaml"), *root.glob("*.yml")]) if root.is_dir() else []
    return {_short_name(f): f for f in files}


def resolve_ontology(ref: str | os.PathLike) -> Path:
    """An existing file path, or the name of a bundled ontology."""
    path = Path(ref).expanduser()
    if path.is_file():
        return path
    bundled = bundled_ontologies()
    if str(ref) in bundled:
        return bundled[str(ref)]
    known = ", ".join(sorted(bundled)) or "none"
    raise OntologyError(f"Ontology `{ref}` is neither a file nor a bundled ontology (bundled: {known})")


def default_ontology_path() -> Path:
    return resolve_ontology(DEFAULT_ONTOLOGY)


@dataclass(frozen=True)
class Settings:
    backend: str
    db_path: str | None  # explicit Ladybug file or ":memory:"; None = default_db_path(ontology name)
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
            db_path=str(explicit_db) if (explicit_db := db_path or env.get("MEMORY_DB_PATH")) else None,
            neo4j_uri=env.get("NEO4J_URI", "bolt://localhost:7687"),
            neo4j_user=env.get("NEO4J_USER", "neo4j"),
            neo4j_password=env.get("NEO4J_PASSWORD", "memory-engine-dev"),
            neo4j_database=env.get("NEO4J_DATABASE") or None,
            ontology_path=resolve_ontology(ontology) if ontology else default_ontology_path(),
            actor=env.get("MEMORY_ACTOR") or None,
        )
