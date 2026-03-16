# Key Dependencies

| Package | Purpose |
|---------|---------|
| `anthropic` | Claude API client — used for all agent invocations |
| `fastapi` + `uvicorn` | API server and ASGI runner |
| `networkx` | DAG construction, traversal, and topological sorting |
| `pydantic` + `pydantic-settings` | Data models, config, and env-file loading |
| `aiosqlite` | Async SQLite for orchestrator state persistence |
| `httpx` | Async HTTP client for GitHub REST API calls |

## Dev Dependencies

| Package | Purpose |
|---------|---------|
| `pytest` | Test runner |
| `pytest-httpx` | Mock httpx calls in integration tests |
| `mypy` | Static type checking |
| `ruff` | Linting and formatting |
