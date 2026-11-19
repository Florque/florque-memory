"""Error types. `code` is what agents see in the JSON error envelope."""

from __future__ import annotations

from collections.abc import Iterable


class MemoryEngineError(Exception):
    code = "error"

    def __init__(self, message: str, issues: Iterable[str] = ()):
        super().__init__(message)
        self.message = message
        self.issues = list(issues)

    def __str__(self) -> str:
        if not self.issues:
            return self.message
        return self.message + "\n" + "\n".join(f"  - {issue}" for issue in self.issues)


class OntologyError(MemoryEngineError):
    """The ontology file itself is invalid or uses unsupported LinkML."""

    code = "ontology_error"


class ValidationError(MemoryEngineError):
    """A request does not fit the ontology."""

    code = "validation_error"


class NotFoundError(MemoryEngineError):
    code = "not_found"


class DatabaseError(MemoryEngineError):
    """The database failed a request (driver-neutral; drivers translate their native errors)."""

    code = "database_error"


class DatabaseUnavailableError(DatabaseError):
    """The database cannot be reached or opened."""

    code = "database_unavailable"


class OntologyMismatchError(DatabaseError):
    """The database was created for a different ontology or project; nothing was written."""

    code = "ontology_mismatch"


class DatabaseBusyError(DatabaseUnavailableError):
    """An embedded database file is locked by another process."""

    code = "database_busy"
