"""Identity stamp: what ontology or project a database was created for.

Each database stores one stamp when it is opened. Opening it later with a different identity (another
domain, another domain prefix, or a project named like a standalone ontology) is refused before any
table is touched. Only identity is compared, never ontology content, so an ontology that grows still
migrates as before.
"""

from __future__ import annotations

from typing import Any

from .errors import OntologyMismatchError
from .parser import Ontology

IDENTITY_SCHEMA = 1


def identity_of(ontology: Ontology) -> dict[str, Any]:
    modules = ontology.annotations.get("modules")
    if not modules:
        return {"schema": IDENTITY_SCHEMA, "kind": "ontology", "name": ontology.name}
    return {
        "schema": IDENTITY_SCHEMA,
        "kind": "project",
        "name": ontology.name,
        "work": modules["work"],
        "domain": modules["domain"],
        "domain_prefix": modules["domain_prefix"],
    }


def describe(identity: dict[str, Any]) -> str:
    if identity.get("kind") == "project":
        return (
            f"project `{identity.get('name')}` (work `{identity.get('work')}`, "
            f"domain `{identity.get('domain')}`, prefix `{identity.get('domain_prefix')}`)"
        )
    return f"ontology `{identity.get('name')}`"


def check_identity(stored: dict[str, Any], current: dict[str, Any]) -> None:
    if stored == current:
        return
    raise OntologyMismatchError(
        "This database was created for a different ontology or project. Nothing was written.",
        [
            f"database was created for {describe(stored)}",
            f"it was opened with {describe(current)}",
            "use a different project name or --db, or restore the ontology or manifest it was created with",
        ],
    )
