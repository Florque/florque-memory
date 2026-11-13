"""Graph storage backends. `open_driver` picks one from Settings; drivers are imported lazily
so the Neo4j client is only needed when MEMORY_BACKEND=neo4j."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.errors import DatabaseUnavailableError
from .base import Column, Edge, GraphDriver, GraphSchema, Node, RelTable

if TYPE_CHECKING:
    from ..config import Settings
    from ..core.parser import Ontology

__all__ = ["Column", "Edge", "GraphDriver", "GraphSchema", "Node", "RelTable", "open_driver"]


def open_driver(settings: Settings, ontology: Ontology, lock_timeout: float = 5.0) -> GraphDriver:
    """Connect to the configured backend and fail fast if it is unusable.

    Ladybug is embedded, so its tables are created/migrated on open (cheap and idempotent)
    and no separate `init` step is needed. Neo4j keeps `mem init` for its constraints.
    """
    if settings.backend == "neo4j":
        try:
            from .neo4j_driver import Neo4jDynamicDriver
        except ImportError as exc:
            raise DatabaseUnavailableError(
                "The Neo4j backend needs the neo4j client: pip install 'memory-engine[neo4j]'"
            ) from exc
        driver = Neo4jDynamicDriver(
            settings.neo4j_uri,
            (settings.neo4j_user, settings.neo4j_password),
            settings.neo4j_database,
            ontology.identifier,
            connection_timeout=5,
            max_transaction_retry_time=5,
        )
        try:
            driver.verify()  # fail fast instead of entering the driver's retry loop
        except Exception:
            driver.close()
            raise
        return driver

    from ..config import default_db_path
    from ..core.schema import schema_for
    from .ladybug_driver import LadybugDriver

    path = settings.db_path or default_db_path(ontology.name)
    driver = LadybugDriver(path, ontology.identifier, lock_timeout=lock_timeout)
    try:
        driver.ensure_schema(schema_for(ontology))
    except Exception:
        driver.close()
        raise
    return driver
