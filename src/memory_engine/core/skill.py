"""Generate an Agent Skill for an ontology: SKILL.md, references/ontology.md, a copy of the
ontology YAML and a `scripts/mem` wrapper pinned to it. Everything is derived from the YAML by
templates, so the skill always matches the ontology it was built from.
"""

from __future__ import annotations

import json
import shlex
import shutil
import uuid

import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import slug
from .engine import STATUS_SLOT, SYSTEM_PROPERTIES
from .errors import MemoryEngineError
from .guide import default_status, render_markdown, supports_archiving
from .parser import (
    TYPE_KINDS,
    ClassDef,
    Ontology,
    Slot,
    load_ontology,
    static_requirements,
)

MARKER = ".memory-engine-skill"
SKILL_SUFFIX = "-memory"


def skill_name(ontology: Ontology) -> str:
    return f"{slug(ontology.name)}{SKILL_SUFFIX}"


# ---------------------------------------------------------------- example payloads


@dataclass
class Example:
    cls: str
    value: str | None  # discriminator value
    payload: dict[str, Any]
    needs: list[int] = field(default_factory=list)  # indexes of examples its edges point to

    @property
    def id(self) -> str:
        return str(self.payload["id"])


_PLACEHOLDERS = {
    "integer": 1,
    "float": 1.5,
    "boolean": True,
    "date": "2027-01-31",
    "datetime": "2027-01-31T12:00:00Z",
}


def _placeholder(ontology: Ontology, cls: ClassDef, slot: Slot) -> Any:
    rng = slot.ranges[0] if slot.ranges else "string"
    if rng in ontology.enums:
        value: Any = next(iter(ontology.enums[rng].values))
    elif TYPE_KINDS.get(rng, "string") != "string":
        value = _PLACEHOLDERS[TYPE_KINDS[rng]]
    elif slot.name == "title":
        value = f"Example {cls.name}"
    elif slot.name == "summary":
        value = f"One to three sentences on what this {cls.name} is and why it matters."
    else:
        value = f"Example {slot.name.replace('_', ' ')}"
    return [value] if slot.multivalued else value


def _required_props(ontology: Ontology, cls: ClassDef, value: str | None) -> list[Slot]:
    names = [s.name for s in cls.properties if s.required]
    for rule in cls.rules:
        applies = not rule.when or (cls.discriminator is not None and rule.when == {cls.discriminator: value})
        if applies:
            names += [r for r in rule.require if not cls.slots[r].is_edge and r not in names]
    skip = {ontology.identifier, *SYSTEM_PROPERTIES}
    if default_status(ontology):
        skip.add(STATUS_SLOT)  # the engine fills it in
    return [cls.slots[n] for n in names if n not in skip]


def _edge_value(ontology: Ontology, slot: Slot, target: str) -> Any:
    value: Any = target
    if any(p.required for p in slot.edge_properties.values()):
        value = {"id": target}
        for prop in slot.edge_properties.values():
            if prop.required:
                enum = ontology.enums.get(prop.range)
                value[prop.name] = next(iter(enum.values)) if enum else _PLACEHOLDERS.get(
                    TYPE_KINDS.get(prop.range, "string"), "example"
                )
    return [value] if slot.multivalued else value


def _build(ontology: Ontology, cls: ClassDef, value: str | None, made: list[Example]) -> Example | None:
    """An example of this (class, value) whose required edges point at earlier examples, or None."""
    ex_id = "-".join(slug(part) for part in ("example", cls.name, value or "") if part)
    payload: dict[str, Any] = {"id": ex_id}
    for slot in _required_props(ontology, cls, value):
        payload[slot.name] = value if slot.name == cls.discriminator else _placeholder(ontology, cls, slot)
    needs = []
    for name in static_requirements(ontology, cls.name, value):
        refs = ontology.allowed_targets(cls.name, name, value)
        target = next(
            (i for i, ex in enumerate(made) if any(ontology.matches(ex.cls, ex.payload, r) for r in refs)), None
        )
        if target is None:
            return None
        needs.append(target)
        payload[name] = _edge_value(ontology, cls.slots[name], made[target].id)
    return Example(cls.name, value, payload, needs)


def example_payloads(ontology: Ontology) -> list[tuple[str, dict[str, Any]]]:
    """One valid `remember` payload per concrete class, in an order that can be replayed as is.

    Same fixpoint as the parser's grounding check: a (class, value) state can be built once
    every required edge can point at an already built example. Then each class keeps its first
    buildable state plus whatever examples that one depends on."""
    states = [(c, v) for c in ontology.concrete_classes() for v in (ontology.discriminator_values(c.name) or [None])]
    made: list[Example] = []
    pending = list(states)
    progress = True
    while pending and progress:
        progress = False
        for state in list(pending):
            example = _build(ontology, *state, made)
            if example is not None:
                made.append(example)
                pending.remove(state)
                progress = True

    keep: set[int] = set()

    def include(i: int) -> None:
        if i not in keep:
            keep.add(i)
            for j in made[i].needs:
                include(j)

    for cls in ontology.concrete_classes():
        first = next((i for i, ex in enumerate(made) if ex.cls == cls.name), None)
        if first is not None:
            include(first)
    return [(made[i].cls, made[i].payload) for i in sorted(keep)]


# ---------------------------------------------------------------- SKILL.md


def _class_summary(ontology: Ontology, cls: ClassDef) -> str:
    values = ontology.discriminator_values(cls.name)
    return f"{cls.name} ({'/'.join(values)})" if values else cls.name


def skill_description(ontology: Ontology) -> str:
    custom = ontology.annotations.get("skill_description")
    if custom:
        return " ".join(str(custom).split())
    classes = ", ".join(_class_summary(ontology, c) for c in ontology.concrete_classes())
    return (
        f"Persistent memory in a graph database governed by the `{ontology.name}` ontology "
        f"({ontology.title}): {classes}. "
        "Use it before starting work to recall what is already known, how things relate and why they "
        "exist, and to record new findings and items so they persist across sessions and agents."
    )


def _shell_json(payload: dict[str, Any]) -> str:
    return shlex.quote(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _project_section(ontology: Ontology) -> list[str]:
    """For a merged project: which classes are work, which are domain, and which bridges exist.
    Empty for a single ontology."""
    prefix = (ontology.annotations.get("modules") or {}).get("domain_prefix")
    if not prefix:
        return []
    lead = f"{prefix}_"
    concrete = ontology.concrete_classes()
    work = [c.name for c in concrete if not c.name.startswith(lead)]
    domain = [c.name for c in concrete if c.name.startswith(lead)]

    bridges: dict[str, dict] = {}
    for cls in ontology.classes.values():
        if cls.name.startswith(lead):
            continue
        for slot in cls.bridges:
            entry = bridges.setdefault(slot.name, {"from": [], "to": [], "role": slot.annotations.get("target_role")})
            if cls.name not in entry["from"]:
                entry["from"].append(cls.name)
            for target in slot.ranges:
                if target not in entry["to"]:
                    entry["to"].append(target)

    lines = [
        "## Work and domain",
        "",
        "This memory holds two ontologies in one graph:",
        "",
        f"- **Work** (plain names): {', '.join(f'`{c}`' for c in work)}. Plans, goals and what is being done.",
        f"- **Domain** (`{lead}` prefix): {', '.join(f'`{c}`' for c in domain)}. What exists: the knowledge of the system.",
        "",
        "A bare domain name such as `Component` also works when it is unambiguous.",
        "",
    ]
    if bridges:
        lines += [
            "Bridges link the two. They are optional, and each one only points at the domain classes that",
            "play its role. Use them to say which work touches which domain item:",
            "",
        ]
        for slot_name, entry in sorted(bridges.items()):
            sources = ", ".join(f"`{c}`" for c in entry["from"])
            if entry["to"]:
                targets = ", ".join(f"`{t}`" for t in entry["to"])
                lines.append(f"- `{slot_name}` from {sources} to {targets} (role `{entry['role']}`)")
            else:
                lines.append(f"- `{slot_name}` from {sources}: no domain class plays role `{entry['role']}` yet")
        lines.append("")
    else:
        lines += ["No bridges are defined for this project.", ""]
    return lines


def render_skill_md(ontology: Ontology) -> str:
    name = skill_name(ontology)
    classes = ontology.concrete_classes()
    first = classes[0].name
    status = default_status(ontology)
    examples = example_payloads(ontology)
    lenient = next(
        (v for e in ontology.enums.values() for v in e.values if "_" in v), None
    )

    if ontology.containment:
        root = f" up to the root `{ontology.root}`" if ontology.root else ""
        context = (
            f"`mem context <id>` returns the `{ontology.containment}` chain{root}, the node's children, "
            "and every other link of the node and its ancestors."
        )
    else:
        context = "`mem context <id>` returns the node and every node it links to or is linked from."

    pick = " Pick the class by its *Use when* / *Not for* notes."
    workflow = [
        (
            "1. **Know the ontology first.** Read `references/ontology.md` once per session "
            f"(or `mem describe --class {first}` for one class). Use only the classes, slots and values it lists."
            + (pick if any(c.annotations.get("use_when") for c in classes) else "")
        ),
        (
            "2. **Look before you write.** `mem search \"<words>\"` or `mem recall <Class> -w key=value` "
            "to find existing nodes. Update them by `id` instead of creating duplicates."
        ),
        f"3. **Load context before working on an item.** {context}",
        (
            "4. **Record as you go.** Create a node with its edges in one call. Keep text properties short "
            "and self-contained; store long content elsewhere and point to it."
        ),
    ]
    if status:
        workflow.append(
            f"5. **Leave `{STATUS_SLOT}` alone on create.** New nodes start as `{status}` so a human can "
            "accept them. Change it only when the new state is clearly true."
        )
    workflow.append(f"{len(workflow) + 1}. **Check after bulk changes.** `mem validate` reports any violations in the whole graph.")

    lines = [
        "---",
        f"name: {name}",
        f"description: {json.dumps(skill_description(ontology), ensure_ascii=False)}",
        "---",
        "",
        f"# {name}",
        "",
        f"Memory for the **{ontology.title}** ontology.",
        "",
        (
            f"<!-- Generated by memory-engine from the `{ontology.name}` ontology. "
            "Do not edit: change the ontology and rebuild the skill. -->"
        ),
        "",
        "A shared, validated memory graph. Every write is checked against the ontology in",
        "`references/ontology.md`; invalid writes are rejected with the reasons, and nothing is saved.",
        "This skill has its own database, separate from other memory skills.",
        "",
        "Run commands through the wrapper in this skill's directory (it pins this skill's ontology):",
        "",
        "```sh",
        "<skill-dir>/scripts/mem <command> ...",
        "```",
        "",
        "Every command prints one JSON object: `{\"status\": \"success\", ...}` or",
        "`{\"status\": \"error\", \"error\": \"<code>\", \"message\": ..., \"issues\": [...]}`.",
        "Exit code 1 means the request was rejected (fix it using `issues`).",
        "Exit code 2 means the database is unusable: with `\"error\": \"database_busy\"` another process",
        "holds the database file, so wait a few seconds and retry; otherwise report the message to the user.",
        "",
        *_project_section(ontology),
        "## Workflow",
        "",
        *workflow,
        "",
        "## Commands",
        "",
        "| Command | Purpose |",
        "|---|---|",
        "| `mem describe [--class C] [--format md\\|json]` | Ontology guide |",
        "| `mem remember <Class> -j '<json>'` or `-p key=value ...` | Create/update a node and its edges (upsert by `id`) |",
        "| `mem link <from_id> <edge> <to_id> [-p key=value]` | Add an edge (single-valued edges are replaced) |",
        "| `mem unlink <from_id> <edge> <to_id>` | Remove an edge (refused if the node requires it) |",
        "| `mem recall <Class> [-w slot=value ...] [--limit N]` | Exact-match listing; an edge slot filters by target id |",
        "| `mem search \"<text>\" [--class C]` | Substring search over ids and text fields |",
        "| `mem get <id> [--depth 1-3]` | Node plus neighbours |",
        "| `mem context <id>` | Working context for a node |",
        "| `mem validate` | Check the whole graph |",
        "",
    ]
    if supports_archiving(ontology):
        lines += [
            "Archived nodes are hidden from `recall`, `search` and `context` unless you pass `--include-archived`.",
            "",
        ]
    lines += [
        "## Payload rules",
        "",
        f"- Omit `{ontology.identifier}` on create to get one generated (returned in `node.{ontology.identifier}`). Pass it to update.",
        "- Edge slots go in the same payload as properties: a target id, a list of ids, or",
        "  `{\"id\": \"...\", \"<edge prop>\": ...}` objects. An edge slot in the payload **replaces**",
        "  that slot's edges; omitted edge slots are kept.",
        "- `null` clears an optional property. `created_*` / `updated_*` are engine-managed.",
        "- Dates are ISO (`2027-03-31`)."
        + (f" Enum values are matched leniently (`{lenient.replace('_', ' ').title()}` → `{lenient}`)." if lenient else ""),
        "",
    ]
    if examples:
        lines += ["## Examples", "", "A valid sequence of writes (placeholders in the values):", "", "```sh"]
        lines += [f"mem remember {cls} -j {_shell_json(payload)}" for cls, payload in examples]
        edges = {s.name for c in classes for s in c.edges}
        richest = max(examples, key=lambda e: sum(1 for k in e[1] if k in edges))
        lines += [f"mem context {richest[1]['id']}", "```", ""]
    lines += [
        "Set `MEMORY_ACTOR=<your actor id>` in the command environment to attribute writes",
        "(`created_by` / `updated_by`) to you.",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- build


def _source_checkout() -> Path | None:
    """The repo this package runs from, if it is an editable/source checkout."""
    root = Path(__file__).resolve().parents[3]
    return root if (root / "pyproject.toml").is_file() and (root / "src" / "memory_engine").is_dir() else None


def render_wrapper(ontology: Ontology, engine_home: Path | None, project: bool = False) -> str:
    fallback = (
        f"default_home={shlex.quote(str(engine_home))}\n"
        'exec uv run --quiet --project "${MEMORY_ENGINE_HOME:-$default_home}" memory-engine "$@"\n'
        if engine_home
        else 'echo "memory-engine is not installed (uv tool install memory-engine)" >&2\nexit 127\n'
    )
    if project:
        pin = (
            'unset MEMORY_ONTOLOGY\n'
            'export MEMORY_PROJECT="$skill_dir/references/project.yaml"\n'
        )
        kind = "project"
    else:
        pin = 'export MEMORY_ONTOLOGY="$skill_dir/references/ontology.yaml"\n'
        kind = "ontology"
    return (
        "#!/usr/bin/env sh\n"
        f"# Generated by memory-engine for the `{ontology.name}` {kind}; do not edit.\n"
        "# Pins this skill's ontology, then runs the global memory-engine CLI if installed,\n"
        "# otherwise the source checkout via uv (override with MEMORY_ENGINE_HOME).\n"
        "set -e\n"
        'skill_dir="$(cd "$(dirname "$0")/.." && pwd -P)"\n'
        + pin
        + "if command -v memory-engine >/dev/null 2>&1; then\n"
        '  exec memory-engine "$@"\n'
        "fi\n" + fallback
    )


def _prepare_out(out_dir: str | Path) -> Path:
    out = Path(out_dir).expanduser().absolute()
    if out.exists() and not (out / MARKER).is_file():
        raise MemoryEngineError(f"{out} exists and was not generated by memory-engine; refusing to overwrite it")
    return out


def _write_skill(
    out: Path,
    ontology: Ontology,
    *,
    texts: dict[str, str],
    copies: list[tuple[Path, str]],
    marker: dict[str, Any],
    engine_home: Path | None,
    project: bool,
) -> Path:
    """Write a whole skill to a temporary directory, then swap it in: a failed build leaves nothing behind."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.parent / f".{out.name}.tmp-{uuid.uuid4().hex[:8]}"
    try:
        (tmp / "references").mkdir(parents=True)
        (tmp / "scripts").mkdir()
        for rel, text in texts.items():
            (tmp / rel).write_text(text, encoding="utf-8")
        for src, rel in copies:
            shutil.copyfile(src, tmp / rel)
        wrapper = tmp / "scripts" / "mem"
        wrapper.write_text(render_wrapper(ontology, engine_home or _source_checkout(), project), encoding="utf-8")
        wrapper.chmod(0o755)
        (tmp / MARKER).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
        old = out.parent / f".{out.name}.old-{uuid.uuid4().hex[:8]}"
        if out.exists():
            out.rename(old)
        tmp.rename(out)
        shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def build_skill(ontology_path: str | Path, out_dir: str | Path, engine_home: Path | None = None) -> Path:
    """Write the skill to `out_dir`, replacing a skill built there before (and nothing else)."""
    source = Path(ontology_path)
    ontology = load_ontology(source)
    out = _prepare_out(out_dir)
    return _write_skill(
        out,
        ontology,
        texts={
            "SKILL.md": render_skill_md(ontology),
            "references/ontology.md": render_markdown(ontology),
        },
        copies=[(source, "references/ontology.yaml")],
        marker={"ontology": ontology.name, "source": str(source.resolve())},
        engine_home=engine_home,
        project=False,
    )


def build_project_skill(manifest_path: str | Path, out_dir: str | Path, engine_home: Path | None = None) -> Path:
    """Write a project skill: the manifest and both ontologies copied into references/, so the skill
    stays self-contained, and a wrapper that pins MEMORY_PROJECT to them."""
    from ..config import load_manifest
    from .project import load_project

    manifest = load_manifest(manifest_path)
    ontology = load_project(manifest_path)
    out = _prepare_out(out_dir)
    # the copy lives inside the skill, so the database path is written already resolved
    copied = {
        "name": manifest.name,
        "work": "work.yaml",
        "domain": "domain.yaml",
        "domain_prefix": manifest.domain_prefix,
    }
    if manifest.db is not None:
        copied["db"] = manifest.db
    project_yaml = yaml.safe_dump(copied, sort_keys=False)
    return _write_skill(
        out,
        ontology,
        texts={
            "SKILL.md": render_skill_md(ontology),
            "references/ontology.md": render_markdown(ontology),
            "references/project.yaml": project_yaml,
        },
        copies=[(manifest.work, "references/work.yaml"), (manifest.domain, "references/domain.yaml")],
        marker={
            "ontology": ontology.name,
            "project": True,
            "source": str(manifest.source),
            "work": str(manifest.work),
            "domain": str(manifest.domain),
            "db": manifest.db,
        },
        engine_home=engine_home,
        project=True,
    )
