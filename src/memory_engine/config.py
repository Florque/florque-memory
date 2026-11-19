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


DEFAULT_WORK_ONTOLOGY = "workgraph"
_MANIFEST_KEYS = {"name", "work", "domain", "domain_prefix", "db"}
DEFAULT_DOMAIN_PREFIX = "dom"


@dataclass(frozen=True)
class ProjectManifest:
    """An umbrella project: one work ontology plus one domain ontology, stored in one database.

    The manifest only references ontologies; it carries no schema content.
    """

    name: str
    work: Path
    domain: Path
    domain_prefix: str = DEFAULT_DOMAIN_PREFIX
    source: Path | None = None
    db: str | None = None  # absolute database file, or ":memory:"; None = the default data-dir file


def load_manifest(path: str | os.PathLike) -> ProjectManifest:
    """Read a project manifest (`name`, `work`, `domain`). Relative paths resolve against the manifest.

    `work` defaults to the bundled `workgraph` ontology. Both ontologies may be a bundled name or a file.
    """
    import yaml  # local import: only project mode needs YAML here

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise OntologyError(f"Project manifest `{path}` does not exist")
    try:
        data = yaml.safe_load(source.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise OntologyError(f"Project manifest `{source}` is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise OntologyError(f"Project manifest `{source}` must be a mapping")

    unknown = sorted(set(data) - _MANIFEST_KEYS)
    if unknown:
        raise OntologyError(f"Project manifest `{source}` has unknown keys: {', '.join(unknown)}")

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise OntologyError(f"Project manifest `{source}` needs a non-empty `name`")
    if not data.get("domain"):
        raise OntologyError(f"Project manifest `{source}` needs a `domain` ontology")

    def locate(key: str, ref: str) -> Path:
        if not isinstance(ref, str):
            raise OntologyError(f"`{key}` in `{source}` must be a bundled name or a file path")
        candidate = Path(ref).expanduser()
        if not candidate.is_absolute() and (source.parent / candidate).is_file():
            return (source.parent / candidate).resolve()
        return resolve_ontology(ref)

    work = locate("work", data.get("work") or DEFAULT_WORK_ONTOLOGY)
    domain = locate("domain", data["domain"])
    if work == domain:
        raise OntologyError("`work` and `domain` must be different ontologies")
    prefix = data.get("domain_prefix", DEFAULT_DOMAIN_PREFIX)
    if not isinstance(prefix, str):
        raise OntologyError(f"`domain_prefix` in `{source}` must be a string")
    db = _manifest_db(source, data.get("db"))
    return ProjectManifest(
        name=name.strip(), work=work, domain=domain, domain_prefix=prefix, source=source, db=db
    )


def _manifest_db(source: Path, raw: Any) -> str | None:
    """`db` is resolved against the manifest's folder, so a project can keep its graph next to it."""
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise OntologyError(f"`db` in `{source}` must be a file path or `:memory:`")
    if raw.strip() == ":memory:":
        return ":memory:"
    candidate = Path(raw.strip()).expanduser()
    if not candidate.is_absolute():
        candidate = source.parent / candidate
    return str(candidate.resolve())


@dataclass(frozen=True)
class Settings:
    backend: str
    db_path: str | None  # explicit Ladybug file or ":memory:"; None = default_db_path(ontology name)
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    neo4j_database: str | None
    ontology_path: Path | None  # None in project mode
    project_path: Path | None  # project manifest; set means work + domain ontologies, merged
    actor: str | None

    @classmethod
    def from_env(
        cls,
        ontology_path: str | os.PathLike | None = None,
        db_path: str | os.PathLike | None = None,
        backend: str | None = None,
        project: str | os.PathLike | None = None,
    ) -> Settings:
        """Explicit arguments (CLI options) win over environment variables, which win over defaults.

        Database file precedence: --db / MEMORY_DB_PATH, then the manifest's `db`, then the data dir.
        """
        env = os.environ
        ontology = ontology_path or env.get("MEMORY_ONTOLOGY")
        project = project or env.get("MEMORY_PROJECT")
        if project and ontology_path:
            raise ValueError("Use either --ontology or --project, not both")
        explicit_db = db_path or env.get("MEMORY_DB_PATH")
        project_path = Path(project).expanduser().resolve() if project else None
        manifest_db = load_manifest(project_path).db if project_path and not explicit_db else None
        backend = (backend or env.get("MEMORY_BACKEND") or DEFAULT_BACKEND).strip().lower()
        if backend not in BACKENDS:
            raise ValueError(f"Unknown MEMORY_BACKEND {backend!r}; expected one of {', '.join(BACKENDS)}")
        return cls(
            backend=backend,
            db_path=str(explicit_db) if explicit_db else manifest_db,
            neo4j_uri=env.get("NEO4J_URI", "bolt://localhost:7687"),
            neo4j_user=env.get("NEO4J_USER", "neo4j"),
            neo4j_password=env.get("NEO4J_PASSWORD", "memory-engine-dev"),
            neo4j_database=env.get("NEO4J_DATABASE") or None,
            ontology_path=None if project else (resolve_ontology(ontology) if ontology else default_ontology_path()),
            project_path=project_path,
            actor=env.get("MEMORY_ACTOR") or None,
        )
