"""`memory-engine` / `mem` command line. Every command prints one JSON envelope
(`{"status": "success" | "error", ...}`) except `describe --format md`.
Exit codes: 0 success, 1 rejected request, 2 database unreachable, busy or failing."""

from __future__ import annotations

import functools
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import click

from .config import BACKENDS, Settings, data_dir, default_db_path, resolve_ontology
from .core.engine import OntologyEngine
from .core.errors import DatabaseError, MemoryEngineError
from .core.guide import describe_json, render_markdown
from .core.identity import identity_of
from .core.parser import Ontology, load_ontology
from .core.project import load_project
from .core.skill import build_project_skill, build_skill, skill_name
from .database import GraphDriver, open_driver
from .tools import TOOLS, error_envelope


class Runtime:
    """Loads the ontology and opens the database only when a command needs them."""

    def __init__(
        self,
        ontology_path: str | None,
        db_path: str | None = None,
        backend: str | None = None,
        project: str | None = None,
    ):
        self.settings = Settings.from_env(ontology_path, db_path=db_path, backend=backend, project=project)
        self._ontology: Ontology | None = None
        self._driver: GraphDriver | None = None

    @property
    def ontology(self) -> Ontology:
        if self._ontology is None:
            if self.settings.project_path is not None:
                self._ontology = load_project(self.settings.project_path)
            else:
                self._ontology = load_ontology(self.settings.ontology_path)
        return self._ontology

    @property
    def engine(self) -> OntologyEngine:
        if self._driver is None:
            self._driver = open_driver(self.settings, self.ontology)
        return OntologyEngine(self.ontology, self._driver, actor=self.settings.actor)

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _emit(payload: dict[str, Any], code: int = 0) -> None:
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default))
    if code:
        click.get_current_context().exit(code)


def _command(fn):
    """Run a command body and turn its result or error into the JSON envelope."""

    @functools.wraps(fn)
    @click.pass_obj
    def wrapper(rt: Runtime, *args: Any, **kwargs: Any) -> None:
        try:
            result = fn(rt, *args, **kwargs)
        except DatabaseError as exc:  # unavailable, busy (locked file) or failing
            _emit(error_envelope(exc.code, exc.message, exc.issues), 2)
        except MemoryEngineError as exc:
            _emit(error_envelope(exc.code, exc.message, exc.issues), 1)
        else:
            if result is not None:
                _emit({"status": "success", **result})

    return wrapper


def _parse_pairs(pairs: tuple[str, ...]) -> dict[str, Any]:
    """`key=value` pairs; JSON values for `[`/`{`/`null`, repeated keys collect into a list."""
    result: dict[str, Any] = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep or not key.strip():
            raise click.BadParameter(f"expected key=value, got {pair!r}")
        value: Any = raw
        if raw.strip().startswith(("[", "{")) or raw.strip() == "null":
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise click.BadParameter(f"{key}: invalid JSON ({exc})") from None
        key = key.strip()
        if key in result:
            existing = result[key]
            result[key] = (existing if isinstance(existing, list) else [existing]) + [value]
        else:
            result[key] = value
    return result


def _parse_json(raw: str | None) -> dict[str, Any]:
    if raw is None:
        return {}
    if raw == "-":
        raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise click.BadParameter(f"invalid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise click.BadParameter("JSON payload must be an object")
    return data


def _run(rt: Runtime, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    return TOOLS[tool].run(rt.engine, args)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--ontology",
    help="LinkML ontology file or bundled ontology name (default: $MEMORY_ONTOLOGY or `workgraph`).",
)
@click.option(
    "--project",
    help="Project manifest (work + domain ontologies in one graph); default: $MEMORY_PROJECT. Excludes --ontology.",
)
@click.option(
    "--db",
    "db_path",
    type=click.Path(dir_okay=False),
    help="Ladybug database file (default: $MEMORY_DB_PATH, else <data dir>/<ontology name>.lbdb).",
)
@click.option(
    "--backend",
    type=click.Choice(BACKENDS),
    help="Graph backend (default: $MEMORY_BACKEND or ladybug).",
)
@click.version_option(package_name="memory-engine")
@click.pass_context
def main(
    ctx: click.Context, ontology: str | None, project: str | None, db_path: str | None, backend: str | None
) -> None:
    """Ontology-governed graph memory for coding agents."""
    logging.getLogger("neo4j").setLevel(logging.ERROR)  # keep stderr clean; errors go in the envelope
    try:
        rt = Runtime(ontology, db_path, backend, project)
    except ValueError as exc:  # e.g. an unknown MEMORY_BACKEND or --ontology with --project
        raise click.UsageError(str(exc)) from None
    except MemoryEngineError as exc:  # e.g. an unknown ontology name
        click.echo(json.dumps(error_envelope(exc.code, exc.message, exc.issues), indent=2))
        ctx.exit(1)
    ctx.obj = rt
    ctx.call_on_close(rt.close)


@main.command()
@_command
def init(rt: Runtime) -> dict[str, Any]:
    """Create the tables (Ladybug) or constraints and indexes (Neo4j) for the ontology (idempotent)."""
    return rt.engine.init()


@main.command()
@click.option("--class", "class_name", help="Describe a single class.")
@click.option("--format", "fmt", type=click.Choice(["md", "json"]), default="md", show_default=True)
@_command
def describe(rt: Runtime, class_name: str | None, fmt: str) -> dict[str, Any] | None:
    """Explain the ontology: classes, slots, edges, allowed values and rules."""
    if fmt == "json":
        return describe_json(rt.ontology, class_name)
    click.echo(render_markdown(rt.ontology, class_name), nl=False)
    return None


@main.command()
@click.argument("class_name", metavar="CLASS")
@click.option("--json", "-j", "raw_json", help="Payload as a JSON object, or - to read stdin.")
@click.option("--prop", "-p", "pairs", multiple=True, help="key=value; repeat a key for a list. Overrides --json.")
@_command
def remember(rt: Runtime, class_name: str, raw_json: str | None, pairs: tuple[str, ...]) -> dict[str, Any]:
    """Create or update a node (upsert by id) with its properties and edges."""
    payload = {**_parse_json(raw_json), **_parse_pairs(pairs)}
    return _run(rt, "remember_entity", {"entity_type": class_name, "properties": payload})


@main.command()
@click.argument("from_id")
@click.argument("slot")
@click.argument("to_id")
@click.option("--prop", "-p", "pairs", multiple=True, help="Edge property key=value, e.g. role=admin.")
@_command
def link(rt: Runtime, from_id: str, slot: str, to_id: str, pairs: tuple[str, ...]) -> dict[str, Any]:
    """Add an edge FROM_ID -[SLOT]-> TO_ID."""
    args = {"from_id": from_id, "relation_type": slot, "to_id": to_id, "properties": _parse_pairs(pairs)}
    return _run(rt, "link_entities", args)


@main.command()
@click.argument("from_id")
@click.argument("slot")
@click.argument("to_id")
@_command
def unlink(rt: Runtime, from_id: str, slot: str, to_id: str) -> dict[str, Any]:
    """Remove the edge FROM_ID -[SLOT]-> TO_ID."""
    return _run(rt, "unlink_entities", {"from_id": from_id, "relation_type": slot, "to_id": to_id})


@main.command()
@click.argument("class_name", metavar="CLASS")
@click.option("--where", "-w", "pairs", multiple=True, help="Filter slot=value (edge slots match a target id).")
@click.option("--limit", default=25, show_default=True)
@click.option("--include-archived", is_flag=True)
@_command
def recall(rt: Runtime, class_name: str, pairs: tuple[str, ...], limit: int, include_archived: bool) -> dict[str, Any]:
    """List nodes of a class."""
    args = {"entity_type": class_name, "filters": _parse_pairs(pairs), "limit": limit, "include_archived": include_archived}
    return _run(rt, "recall_context", args)


@main.command()
@click.argument("text")
@click.option("--class", "class_name", help="Restrict to a class.")
@click.option("--limit", default=25, show_default=True)
@click.option("--include-archived", is_flag=True)
@_command
def search(rt: Runtime, text: str, class_name: str | None, limit: int, include_archived: bool) -> dict[str, Any]:
    """Find nodes whose id or text properties contain TEXT."""
    args = {"text": text, "entity_type": class_name, "limit": limit, "include_archived": include_archived}
    return _run(rt, "search_memory", args)


@main.command()
@click.argument("node_id", metavar="ID")
@click.option("--depth", default=1, show_default=True, type=click.IntRange(0, 3))
@_command
def get(rt: Runtime, node_id: str, depth: int) -> dict[str, Any]:
    """Show a node and its neighbourhood."""
    return _run(rt, "get_entity", {"id": node_id, "depth": depth})


@main.command()
@click.argument("node_id", metavar="ID")
@click.option("--include-archived", is_flag=True)
@_command
def context(rt: Runtime, node_id: str, include_archived: bool) -> dict[str, Any]:
    """Everything to know before working on a node."""
    return _run(rt, "get_context", {"id": node_id, "include_archived": include_archived})


@main.command()
@_command
def validate(rt: Runtime) -> dict[str, Any]:
    """Check the whole graph against the ontology."""
    return _run(rt, "validate_memory", {})


@main.command()
@click.argument("tool", type=click.Choice(sorted(TOOLS)))
@click.argument("arguments", required=False)
@_command
def call(rt: Runtime, tool: str, arguments: str | None) -> dict[str, Any]:
    """Invoke a tool by name with a JSON ARGUMENTS object (or - for stdin)."""
    return _run(rt, tool, _parse_json(arguments))


@main.command("db-path")
@_command
def db_path_cmd(rt: Runtime) -> dict[str, Any]:
    """Show the database file this ontology uses (and the Ladybug version), without opening it."""
    from importlib.metadata import PackageNotFoundError, version

    path = rt.settings.db_path or str(default_db_path(rt.ontology.name))
    try:
        ladybug_version = version("ladybug")
    except PackageNotFoundError:
        ladybug_version = None
    return {
        "ontology": rt.ontology.name,
        "backend": rt.settings.backend,
        "path": path,
        "exists": path != ":memory:" and Path(path).exists(),
        "ladybug_version": ladybug_version,
        "identity": identity_of(rt.ontology),  # what this ontology stamps; the stored stamp is checked on open
    }


@main.group()
def skill() -> None:
    """Generate Agent Skills from ontologies."""


@skill.command("build")
@click.argument("ontology_ref", metavar="ONTOLOGY")
@click.option("--out", type=click.Path(file_okay=False), help="Skill directory (default: <data dir>/skills/<name>-memory).")
@_command
def skill_build(rt: Runtime, ontology_ref: str, out: str | None) -> dict[str, Any]:
    """Generate the skill (SKILL.md, references, scripts/mem) for ONTOLOGY, a file or bundled name."""
    source = resolve_ontology(ontology_ref)
    ontology = load_ontology(source)
    name = skill_name(ontology)
    path = build_skill(source, Path(out) if out else data_dir() / "skills" / name)
    return {"skill": name, "path": str(path), "ontology": ontology.name, "source": str(source.resolve())}


@skill.command("build-project")
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False), metavar="MANIFEST")
@click.option("--out", type=click.Path(file_okay=False), help="Skill directory (default: <data dir>/skills/<name>-memory).")
@_command
def skill_build_project(rt: Runtime, manifest: str, out: str | None) -> dict[str, Any]:
    """Generate one skill for a project: MANIFEST names the work and domain ontologies."""
    ontology = load_project(manifest)
    name = skill_name(ontology)
    path = build_project_skill(manifest, Path(out) if out else data_dir() / "skills" / name)
    return {
        "skill": name,
        "path": str(path),
        "ontology": ontology.name,
        "project": True,
        "source": str(Path(manifest).resolve()),
    }


@main.command()
def tools() -> None:
    """List tools and their arguments as JSON (for agent frameworks)."""
    _emit({
        "status": "success",
        "tools": [{"name": t.name, "description": t.description, "params": t.params} for t in TOOLS.values()],
    })


if __name__ == "__main__":
    main()
