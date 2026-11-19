# Agent tools (`tools.py`)

A registry of named tools that take and return JSON. It is the interface for agent frameworks that call
tools by name rather than through the shell.

## Usage

```sh
mem tools                                  # list tool names and their arguments
mem call <tool> '<json arguments>'         # invoke one tool
```

From Python:

```python
from memory_engine.tools import handle_agent_call

handle_agent_call(engine, "get_context", {"id": "init-wizard"})
```

`handle_agent_call` returns the same envelope as the CLI: `{"status": "success", ...}` or
`{"status": "error", "error": ..., "message": ..., "issues": [...]}`. An unknown tool returns
`unknown_tool` with the list of tools.

## Registered tools

| Tool | Engine operation |
|---|---|
| `remember_entity` | `remember` |
| `link_entities` | `link` |
| `unlink_entities` | `unlink` |
| `recall_context` | `recall` |
| `search_memory` | `search` |
| `get_entity` | `get` |
| `get_context` | `context` |
| `validate_memory` | `validate` |

## Structure

- `Tool` has a name, a description, a parameter map (a trailing `?` marks an optional argument), and a
  function that receives the engine and the arguments.
- `TOOLS` is the registry. Each tool calls one engine operation, so the tools add no behaviour of their own.
- `error_envelope(code, message, issues)` builds the error shape for any failure.

## Related

- [Engine](engine.md): the operations the tools call.
- [CLI](cli.md): the same operations as commands.
