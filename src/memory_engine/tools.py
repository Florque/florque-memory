"""Tool surface for agents. Each tool takes a JSON object of arguments and returns JSON-able data.

The CLI exposes these as subcommands; `handle_agent_call` is the generic entry point
for agent frameworks that call tools by name with JSON arguments.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .core.engine import OntologyEngine
from .core.errors import MemoryEngineError, ValidationError


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: dict[str, str]  # name -> description; a trailing "?" in the name marks it optional
    run: Callable[[OntologyEngine, Mapping[str, Any]], dict[str, Any]]


def _arg(args: Mapping[str, Any], name: str) -> Any:
    if name not in args:
        raise ValidationError(f"Missing argument `{name}`")
    return args[name]


TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in [
        Tool(
            "remember_entity",
            "Create or update a node, with its properties and outgoing edges, in one validated write.",
            {"entity_type": "Ontology class", "properties": "Slot values, including edge slots as target ids"},
            lambda e, a: e.remember(_arg(a, "entity_type"), _arg(a, "properties")),
        ),
        Tool(
            "link_entities",
            "Add an edge between two existing nodes.",
            {
                "from_id": "Source node id",
                "relation_type": "Edge slot, e.g. depends_on",
                "to_id": "Target node id",
                "properties?": "Edge properties, e.g. {\"role\": \"admin\"}",
            },
            lambda e, a: e.link(_arg(a, "from_id"), _arg(a, "relation_type"), _arg(a, "to_id"), a.get("properties")),
        ),
        Tool(
            "unlink_entities",
            "Remove an edge, unless the source node requires it.",
            {"from_id": "Source node id", "relation_type": "Edge slot", "to_id": "Target node id"},
            lambda e, a: e.unlink(_arg(a, "from_id"), _arg(a, "relation_type"), _arg(a, "to_id")),
        ),
        Tool(
            "recall_context",
            "List nodes of a class matching exact filters (property values, or edge slot -> target id).",
            {"entity_type": "Ontology class", "filters?": "{slot: value}", "limit?": "Default 25", "include_archived?": "bool"},
            lambda e, a: {
                "data": e.recall(
                    _arg(a, "entity_type"), a.get("filters"), a.get("limit", 25), bool(a.get("include_archived"))
                )
            },
        ),
        Tool(
            "search_memory",
            "Case-insensitive text search over ids and free-text properties.",
            {"text": "Search text", "entity_type?": "Restrict to a class", "limit?": "Default 25", "include_archived?": "bool"},
            lambda e, a: {
                "data": e.search(
                    _arg(a, "text"), a.get("entity_type"), a.get("limit", 25), bool(a.get("include_archived"))
                )
            },
        ),
        Tool(
            "get_entity",
            "A node with its neighbours up to `depth` hops.",
            {"id": "Node id", "depth?": "1-3, default 1"},
            lambda e, a: e.get(_arg(a, "id"), int(a.get("depth", 1))),
        ),
        Tool(
            "get_context",
            "Everything to know before working on a node: ancestors, children, goals, owners, documents, signals.",
            {"id": "Node id", "include_archived?": "bool"},
            lambda e, a: e.context(_arg(a, "id"), bool(a.get("include_archived"))),
        ),
        Tool(
            "validate_memory",
            "Check the whole graph against the ontology.",
            {},
            lambda e, a: e.validate(),
        ),
    ]
}


def handle_agent_call(engine: OntologyEngine, tool_name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Run a tool and wrap the result in the `{"status": ...}` envelope agents expect."""
    tool = TOOLS.get(tool_name)
    if tool is None:
        return {"status": "error", "error": "unknown_tool", "message": f"Unknown memory tool: {tool_name}", "tools": list(TOOLS)}
    try:
        return {"status": "success", **tool.run(engine, arguments or {})}
    except MemoryEngineError as exc:
        return error_envelope(exc.code, exc.message, exc.issues)


def error_envelope(code: str, message: str, issues: list[str] | None = None) -> dict[str, Any]:
    envelope: dict[str, Any] = {"status": "error", "error": code, "message": message}
    if issues:
        envelope["issues"] = issues
    return envelope
