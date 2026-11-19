# Errors (`core/errors.py`)

Every expected failure is a `MemoryEngineError` with a `code`, a message and optional `issues`. The CLI
and the tools turn it into the JSON error envelope, so agents see the same shape for every failure.

## Types and codes

| Class | Code | Exit | Raised when |
|---|---|---|---|
| `OntologyError` | `ontology_error` | 1 | The ontology or manifest is invalid or uses unsupported LinkML |
| `ValidationError` | `validation_error` | 1 | A request does not fit the ontology; `issues` lists each problem |
| `NotFoundError` | `not_found` | 1 | A node or a link target does not exist |
| `CastError` (`casting.py`) | reported as validation | 1 | A value cannot be converted to its declared type |
| `DatabaseError` | `database_error` | 2 | The database failed a request |
| `DatabaseUnavailableError` | `database_unavailable` | 2 | The database cannot be reached or opened |
| `DatabaseBusyError` | `database_busy` | 2 | An embedded file is locked by another process; retry shortly |
| `DatabaseAuthError` (Neo4j) | `database_auth` | 2 | Wrong Neo4j credentials |
| `OntologyMismatchError` | `ontology_mismatch` | 2 | The database belongs to another ontology or project; nothing was written |

## Envelope

```json
{
  "status": "error",
  "error": "validation_error",
  "message": "Invalid dom_Finding payload",
  "issues": ["`title` is not a slot of dom_Finding; allowed: ..."]
}
```

`str(error)` joins the message and the issues, so a plain traceback is still readable.

## Related

- [CLI](cli.md): exit codes.
- [Database identity](../guides/database-identity.md): the mismatch error in detail.
