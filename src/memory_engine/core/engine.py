"""OntologyEngine: validates agent requests against the ontology and applies them to the graph."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..database.base import Edge, EdgeTargets, GraphDriver, Node
from .casting import CastError, cast_filter, cast_slot, cast_value, is_text_slot
from .errors import NotFoundError, ValidationError
from .parser import ClassDef, Ontology, ParentRef, Slot

SYSTEM_PROPERTIES = frozenset({"created_at", "updated_at", "created_by", "updated_by"})
STATUS_SLOT = "status"
DEFAULT_STATUS = "proposed"
ARCHIVED_STATUS = "archived"
MAX_DEPTH = 3
MAX_CHAIN = 100

EdgeMap = dict[str, EdgeTargets]  # slot name -> [(target id, edge props)]


@dataclass(frozen=True)
class Link:
    """An outgoing edge as validation sees it. `target` is None when the target node is missing."""

    dst: str
    target: Node | None
    props: Mapping[str, Any]


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def _user_props(props: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in props.items() if k not in SYSTEM_PROPERTIES}


def _label(ontology: Ontology, node: Node) -> str:
    cls = ontology.classes.get(node.cls)
    if cls and cls.discriminator and node.props.get(cls.discriminator):
        return f"{node.cls}:{node.props[cls.discriminator]}"
    return node.cls


def _check_link(ontology: Ontology, slot: Slot, link: Link, allowed: list[ParentRef]) -> list[str]:
    if link.target is None:
        return [f"`{slot.name}` target `{link.dst}` does not exist"]
    issues = []
    if not any(ontology.matches(link.target.cls, link.target.props, ref) for ref in allowed):
        issues.append(
            f"`{slot.name}` cannot point to `{link.dst}` ({_label(ontology, link.target)}); "
            f"allowed: {', '.join(map(str, allowed))}"
        )
    for key, prop in slot.edge_properties.items():
        value = link.props.get(key)
        if _empty(value):
            if prop.required:
                issues.append(f"`{slot.name}` -> `{link.dst}` needs edge property `{key}`")
            continue
        try:
            cast_value(value, prop.range, ontology.enums)
        except CastError as exc:
            issues.append(f"`{slot.name}` -> `{link.dst}` `{key}`: {exc}")
    for key in _user_props(link.props):
        if key not in slot.edge_properties:
            issues.append(f"`{slot.name}` has no edge property `{key}`")
    return issues


def check_node(
    ontology: Ontology, cls: ClassDef, props: Mapping[str, Any], links: Mapping[str, list[Link]]
) -> list[str]:
    """Every way a node with these properties and outgoing edges violates the ontology."""
    issues: list[str] = []
    value = props.get(cls.discriminator) if cls.discriminator else None

    for key in props:
        if key not in cls.slots and key not in SYSTEM_PROPERTIES:
            issues.append(f"`{key}` is not a property of {cls.name}")
    for slot in cls.properties:
        if slot.identifier or slot.name in SYSTEM_PROPERTIES:
            continue
        current = props.get(slot.name)
        if _empty(current):
            if slot.required:
                issues.append(f"`{slot.name}` is required")
            continue
        try:
            cast_slot(slot, current, ontology.enums)
        except CastError as exc:
            issues.append(f"`{slot.name}`: {exc}")

    # edge slot -> why it is required
    required = {name: f"required for {cls.discriminator}={value}" for name in ontology.required_edges(cls.name, value)}
    for name in required:
        if cls.slots[name].required:
            required[name] = ""
    for rule in cls.rules:
        if not all(props.get(k) is not None and str(props.get(k)) == v for k, v in rule.when.items()):
            continue
        reason = f"required when {rule.condition()}: {rule.description}" if rule.when else rule.description
        for name in rule.require:
            if cls.slots[name].is_edge:
                required.setdefault(name, reason)
            elif _empty(props.get(name)):
                issues.append(f"`{name}` is {reason}")

    for slot in cls.edges:
        found = links.get(slot.name, [])
        if not found and slot.name in required:
            reason = required[slot.name]
            issues.append(f"`{slot.name}` is required" + (f" ({reason})" if reason else ""))
        if len(found) > 1 and not slot.multivalued:
            issues.append(f"`{slot.name}` takes one target, got {len(found)}")
        allowed = ontology.allowed_targets(cls.name, slot.name, value)
        for link in found:
            issues += _check_link(ontology, slot, link, allowed)
    return issues


class OntologyEngine:
    def __init__(self, ontology: Ontology, db: GraphDriver, actor: str | None = None):
        self.ontology = ontology
        self.db = db
        self.actor = actor

    # ---------------------------------------------------------------- helpers

    @property
    def _containment_rel(self) -> str | None:
        return self.ontology.containment.upper() if self.ontology.containment else None

    def _find(self, node_id: str) -> Node | None:
        return self.db.get_nodes([node_id]).get(node_id)

    def _require(self, node_id: str) -> Node:
        node = self._find(node_id)
        if node is None:
            raise NotFoundError(f"No node with {self.ontology.identifier} `{node_id}`")
        return node

    def _class_of(self, node: Node) -> ClassDef:
        cls = self.ontology.classes.get(node.cls)
        if cls is None:
            raise ValidationError(f"`{node.id}` has label {node.cls}, which is not in the ontology")
        return cls

    def _edge_slot(self, cls: ClassDef, name: str) -> Slot:
        slot = cls.slots.get(name.strip().lower())
        if slot is None or not slot.is_edge:
            allowed = ", ".join(s.name for s in cls.edges)
            raise ValidationError(f"{cls.name} has no edge `{name}`; its edges: {allowed}")
        return slot

    def _new_id(self, cls: ClassDef) -> str:
        return f"{cls.name.lower()}-{uuid.uuid4().hex[:8]}"

    def _out_edges(self, node_id: str) -> EdgeMap:
        result: EdgeMap = {}
        for edge in self.db.edges(from_ids=[node_id]):
            slot = self.ontology.slot_for_rel(edge.rel)
            if slot:
                result.setdefault(slot, []).append((edge.dst, _user_props(edge.props)))
        return result

    def _links(self, edges: EdgeMap) -> dict[str, list[Link]]:
        nodes = self.db.get_nodes({dst for targets in edges.values() for dst, _ in targets})
        return {
            slot: [Link(dst, nodes.get(dst), props) for dst, props in targets]
            for slot, targets in edges.items()
        }

    def _ancestors(self, node_id: str) -> list[Node]:
        """Containment chain above `node_id`, nearest parent first."""
        rel = self._containment_rel
        if rel is None:
            return []
        chain: list[Node] = []
        seen = {node_id}
        current = node_id
        for _ in range(MAX_CHAIN):
            parents = self.db.edges(from_ids=[current], rel=rel)
            if not parents or parents[0].dst in seen:
                break
            parent = self._find(parents[0].dst)
            if parent is None:
                break
            chain.append(parent)
            seen.add(parent.id)
            current = parent.id
        return chain

    def _cycle_issues(self, node_id: str, parents: list[str]) -> list[str]:
        return [
            f"`{self.ontology.containment}` -> `{dst}` would create a containment cycle"
            for dst in parents
            if dst == node_id or any(a.id == node_id for a in self._ancestors(dst))
        ]

    def _guard(
        self,
        cls: ClassDef,
        node_id: str,
        before: tuple[Mapping[str, Any], EdgeMap] | None,
        after: tuple[Mapping[str, Any], EdgeMap],
        new_parents: list[str],
    ) -> None:
        """Reject the change if it introduces ontology violations. Pre-existing
        violations unrelated to the change do not block it (`validate` reports them)."""
        issues = check_node(self.ontology, cls, after[0], self._links(after[1]))
        if before is not None:
            existing = set(check_node(self.ontology, cls, before[0], self._links(before[1])))
            issues = [i for i in issues if i not in existing]
        issues += self._cycle_issues(node_id, new_parents)
        if issues:
            raise ValidationError(f"{cls.name} `{node_id}` was not saved: it violates the ontology", issues)

    def _parse_targets(self, slot: Slot, value: Any) -> EdgeTargets:
        """Accept `"id"`, `["id", ...]`, `{"id": ..., <edge props>}` or a list of those."""
        if value is None:
            return []
        id_key = self.ontology.identifier
        targets: dict[str, dict[str, Any]] = {}
        for item in value if isinstance(value, (list, tuple)) else [value]:
            if isinstance(item, Mapping):
                item = dict(item)
                dst = str(item.pop(id_key, "") or "").strip()
                if not dst:
                    raise CastError(f"target objects need `{id_key}`")
                props = {}
                for key, raw in item.items():
                    prop = slot.edge_properties.get(key)
                    if prop is None:
                        allowed = ", ".join(slot.edge_properties) or "none"
                        raise CastError(f"`{slot.name}` has no edge property `{key}` (allowed: {allowed})")
                    props[key] = cast_value(raw, prop.range, self.ontology.enums)
                targets[dst] = props
            elif isinstance(item, (str, int)) and not isinstance(item, bool) and str(item).strip():
                targets[str(item).strip()] = {}
            else:
                raise CastError(f'expected a target id or {{"{id_key}": ...}} object, got {item!r}')
        return list(targets.items())

    def _parse_payload(self, cls: ClassDef, data: Mapping[str, Any]) -> tuple[dict[str, Any], EdgeMap]:
        issues: list[str] = []
        patch: dict[str, Any] = {}
        edges: EdgeMap = {}
        for key, value in data.items():
            if key in SYSTEM_PROPERTIES:
                issues.append(f"`{key}` is managed by the engine and cannot be set")
                continue
            slot = cls.slots.get(key)
            if slot is None:
                issues.append(f"`{key}` is not a slot of {cls.name}; allowed: {', '.join(cls.slots)}")
                continue
            try:
                if slot.is_edge:
                    edges[key] = self._parse_targets(slot, value)
                else:
                    patch[key] = None if value is None else cast_slot(slot, value, self.ontology.enums)
            except CastError as exc:
                issues.append(f"`{key}`: {exc}")
        if issues:
            raise ValidationError(f"Invalid {cls.name} payload", issues)
        return patch, edges

    def _exclude(self, include_archived: bool) -> dict[str, Any]:
        return {} if include_archived else {STATUS_SLOT: ARCHIVED_STATUS}

    def _visible(self, node: Node | None, include_archived: bool) -> bool:
        return node is not None and (include_archived or node.props.get(STATUS_SLOT) != ARCHIVED_STATUS)

    def _dict(self, node: Node) -> dict[str, Any]:
        """Node as a dict in ontology slot order, engine-managed fields last."""
        cls = self.ontology.classes.get(node.cls)
        order = {name: i for i, name in enumerate(cls.slots if cls else ())}
        keys = sorted(node.props, key=lambda k: (k in SYSTEM_PROPERTIES, order.get(k, len(order)), k))
        return {"class": node.cls, **{k: node.props[k] for k in keys}}

    def _brief(self, node: Node) -> dict[str, Any]:
        brief = {"id": node.id, "class": _label(self.ontology, node)}
        if "title" in node.props:
            brief["title"] = node.props["title"]
        return brief

    def _edge_dict(self, edge: Edge) -> dict[str, Any]:
        slot = self.ontology.slot_for_rel(edge.rel) or edge.rel
        return {"from": edge.src, "slot": slot, "to": edge.dst, **_user_props(edge.props)}

    # ---------------------------------------------------------------- writes

    def init(self) -> dict[str, Any]:
        from .schema import schema_for  # schema.py imports this module

        schema = schema_for(self.ontology)
        self.db.ensure_schema(schema)
        labels = schema.labels
        return {"ontology": self.ontology.name, "labels": labels, "identifier": self.ontology.identifier}

    def remember(self, class_name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Create or update a node, including the edges named in the payload.

        Edge slots present in the payload replace that slot's outgoing edges;
        edge slots left out are kept as they are.
        """
        cls = self.ontology.get_class(class_name)
        data = dict(payload)
        id_key = self.ontology.identifier
        node_id = str(data.pop(id_key, "") or "").strip() or self._new_id(cls)
        existing = self._find(node_id)
        if existing is not None and existing.cls != cls.name:
            raise ValidationError(f"`{node_id}` is a {existing.cls}, not a {cls.name}")

        patch, edges = self._parse_payload(cls, data)
        if existing is None and patch.get(STATUS_SLOT) is None and STATUS_SLOT in cls.slots:
            try:
                patch[STATUS_SLOT] = cast_slot(cls.slots[STATUS_SLOT], DEFAULT_STATUS, self.ontology.enums)
            except CastError:
                pass  # this ontology's status has no `proposed`

        before_props = dict(existing.props) if existing else {}
        after_props = {k: v for k, v in {**before_props, **patch}.items() if v is not None}
        after_props[id_key] = node_id
        before_edges = self._out_edges(node_id) if existing else {}
        after_edges = {**before_edges, **edges}
        new_parents = [dst for dst, _ in edges.get(self.ontology.containment or "", [])]
        self._guard(
            cls,
            node_id,
            (before_props, before_edges) if existing else None,
            (after_props, after_edges),
            new_parents,
        )

        create_props: dict[str, Any] = {}
        if self.actor:
            patch["updated_by"] = self.actor
            if existing is None:
                create_props["created_by"] = self.actor
        self.db.write_node(
            node_id,
            cls.name,
            patch,
            create_props,
            {cls.slots[slot].rel_type: targets for slot, targets in edges.items()},
        )
        return {"created": existing is None, "node": self._dict(self._require(node_id))}

    def link(self, src: str, slot_name: str, dst: str, props: Mapping[str, Any] | None = None) -> dict[str, Any]:
        node = self._require(src)
        cls = self._class_of(node)
        slot = self._edge_slot(cls, slot_name)
        try:
            [(dst, edge_props)] = self._parse_targets(slot, {self.ontology.identifier: dst, **(props or {})})
        except CastError as exc:
            raise ValidationError(f"Invalid `{slot.name}` link", [str(exc)]) from None

        before = self._out_edges(src)
        current = before.get(slot.name, [])
        kept = [t for t in current if t[0] != dst] if slot.multivalued else []
        after = {**before, slot.name: kept + [(dst, edge_props)]}
        new_parents = [dst] if slot.name == self.ontology.containment else []
        self._guard(cls, src, (node.props, before), (node.props, after), new_parents)

        self.db.add_edge(src, slot.rel_type, dst, edge_props, single=not slot.multivalued)
        replaced = [t[0] for t in current if t[0] != dst] if not slot.multivalued else []
        return {"edge": {"from": src, "slot": slot.name, "to": dst, **edge_props}, "replaced": replaced}

    def unlink(self, src: str, slot_name: str, dst: str) -> dict[str, Any]:
        node = self._require(src)
        cls = self._class_of(node)
        slot = self._edge_slot(cls, slot_name)
        before = self._out_edges(src)
        current = before.get(slot.name, [])
        if not any(t[0] == dst for t in current):
            raise NotFoundError(f"No `{slot.name}` edge from `{src}` to `{dst}`")
        after = {**before, slot.name: [t for t in current if t[0] != dst]}
        self._guard(cls, src, (node.props, before), (node.props, after), [])
        self.db.remove_edge(src, slot.rel_type, dst)
        return {"removed": {"from": src, "slot": slot.name, "to": dst}}

    # ---------------------------------------------------------------- reads

    def recall(
        self,
        class_name: str,
        filters: Mapping[str, Any] | None = None,
        limit: int | None = 25,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        """Nodes of a class (abstract classes include their subclasses).

        Filter keys are property slots (exact match), edge slots (has an edge to that id),
        the identifier, or created_by/updated_by.
        """
        cls = self.ontology.get_class(class_name, concrete=False)
        id_key = self.ontology.identifier
        props: dict[str, Any] = {}
        edges: dict[str, str] = {}
        issues = []
        for key, value in (filters or {}).items():
            slot = cls.slots.get(key)
            if key in (id_key, "created_by", "updated_by"):
                props[key] = str(value)
            elif slot is None or key in SYSTEM_PROPERTIES:
                issues.append(f"cannot filter {cls.name} by `{key}`; slots: {', '.join(cls.slots)}")
            elif slot.is_edge:
                edges[slot.rel_type] = str(value)
            else:
                try:
                    props[key] = cast_filter(slot, value, self.ontology.enums)
                except CastError as exc:
                    issues.append(f"`{key}`: {exc}")
        if issues:
            raise ValidationError("Invalid filter", issues)
        nodes = self.db.find_nodes(
            labels=self.ontology.concrete_subclasses(cls.name),
            filters=props,
            edge_filters=edges,
            exclude=self._exclude(include_archived),
            limit=limit,
        )
        return [self._dict(n) for n in nodes]

    def search(
        self, text: str, class_name: str | None = None, limit: int | None = 25, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        """Case-insensitive substring search over the identifier and free-text properties."""
        if not text.strip():
            raise ValidationError("Search text is empty")
        if class_name:
            labels = self.ontology.concrete_subclasses(self.ontology.get_class(class_name, concrete=False).name)
        else:
            labels = [c.name for c in self.ontology.concrete_classes()]
        keys = {self.ontology.identifier}
        for label in labels:
            keys.update(s.name for s in self.ontology.classes[label].properties if is_text_slot(s))
        nodes = self.db.find_nodes(
            labels=labels,
            text=text.strip(),
            text_keys=sorted(keys),
            exclude=self._exclude(include_archived),
            limit=limit,
        )
        return [self._dict(n) for n in nodes]

    def get(self, node_id: str, depth: int = 1) -> dict[str, Any]:
        """A node plus everything within `depth` hops, in either direction."""
        node = self._require(node_id)
        seen = {node_id}
        frontier = {node_id}
        edges: dict[tuple[str, str, str], Edge] = {}
        for _ in range(max(0, min(depth, MAX_DEPTH))):
            if not frontier:
                break
            found = self.db.edges(from_ids=frontier) + self.db.edges(to_ids=frontier)
            reached = set()
            for edge in found:
                edges[(edge.src, edge.rel, edge.dst)] = edge
                reached.update({edge.src, edge.dst} - seen)
            seen |= reached
            frontier = reached
        nodes = self.db.get_nodes(seen - {node_id})
        return {
            "node": self._dict(node),
            "neighbours": [self._dict(n) for n in nodes.values()],
            "edges": [self._edge_dict(e) for e in edges.values()],
        }

    def context(self, node_id: str, include_archived: bool = False) -> dict[str, Any]:
        """What an agent should know before working on `node_id`: its containment chain
        up to the root, its children, and every non-containment link of the node and its
        ancestors (goals served, owners, dependencies, documents and signals about them)."""
        node = self._require(node_id)
        chain = self._ancestors(node_id)
        chain_ids = [node_id] + [a.id for a in chain]
        rel = self._containment_rel

        outgoing = [e for e in self.db.edges(from_ids=chain_ids) if e.rel != rel]
        incoming = self.db.edges(to_ids=chain_ids)
        children = [e for e in incoming if e.rel == rel and e.dst == node_id]
        incoming = [e for e in incoming if e.rel != rel]
        nodes = self.db.get_nodes(
            {e.dst for e in outgoing} | {e.src for e in incoming} | {e.src for e in children}
        )

        links = []
        for direction, edge, other_id, own_id in (
            [("out", e, e.dst, e.src) for e in outgoing] + [("in", e, e.src, e.dst) for e in incoming]
        ):
            other = nodes.get(other_id)
            if not self._visible(other, include_archived):
                continue
            entry = {
                "direction": direction,
                "slot": self.ontology.slot_for_rel(edge.rel) or edge.rel,
                "via": None if own_id == node_id else own_id,
                "node": self._dict(other),
            }
            if edge_props := _user_props(edge.props):
                entry["edge"] = edge_props
            if direction == "out" and (path := self._ancestors(other.id)):
                entry["path"] = [self._brief(a) for a in path]
            links.append(entry)

        return {
            "node": self._dict(node),
            "ancestors": [self._dict(a) for a in chain],
            "children": [
                self._dict(nodes[e.src]) for e in children if self._visible(nodes.get(e.src), include_archived)
            ],
            "links": links,
        }

    def validate(self) -> dict[str, Any]:
        """Check every stored node and edge against the ontology."""
        nodes = self.db.find_nodes()
        edges = self.db.edges()
        by_id = {n.id: n for n in nodes}
        issues: list[dict[str, Any]] = []

        out: dict[str, dict[str, list[Link]]] = {}
        for edge in edges:
            slot = self.ontology.slot_for_rel(edge.rel)
            if slot is None:
                issues.append({"id": edge.src, "issue": f"relationship {edge.rel} -> `{edge.dst}` is not in the ontology"})
                continue
            out.setdefault(edge.src, {}).setdefault(slot, []).append(Link(edge.dst, by_id.get(edge.dst), edge.props))

        for node in nodes:
            cls = self.ontology.classes.get(node.cls)
            if cls is None or cls.abstract:
                issues.append({"id": node.id, "class": node.cls, "issue": f"label {node.cls} is not a concrete class"})
                continue
            links = out.get(node.id, {})
            problems = [f"`{s}` is not an edge of {cls.name}" for s in links if s not in cls.slots]
            problems += check_node(self.ontology, cls, node.props, links)
            issues += [{"id": node.id, "class": node.cls, "issue": p} for p in problems]

        # containment cycles: follow first parent until the chain ends or repeats
        parent = {}
        if self._containment_rel:
            parent = {e.src: e.dst for e in edges if e.rel == self._containment_rel}
        reported: set[frozenset[str]] = set()
        for start in parent:
            path: list[str] = []
            current: str | None = start
            while current in parent and current not in path:
                path.append(current)
                current = parent[current]
            if current in path:
                cycle = path[path.index(current):]
                if frozenset(cycle) not in reported:
                    reported.add(frozenset(cycle))
                    issues.append({"id": current, "issue": "containment cycle: " + " -> ".join(cycle + [current])})

        return {"valid": not issues, "checked": {"nodes": len(nodes), "edges": len(edges)}, "issues": issues}
