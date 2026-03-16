# Setup

## Prerequisites

- Python 3.11
- A GitHub personal access token with `repo` scope
- An Anthropic API key

## Install

```bash
# Create and activate the venv
python3.11 -m venv /workspaces/.venvs/agent_agent
source /workspaces/.venvs/agent_agent/bin/activate

# Install the package and dev dependencies
pip install -e ".[dev]"
```

## Environment

Copy the dev env file and fill in your credentials:

```bash
cp .env.dev .env.dev.local
```

Required values in `.env.dev`:

```
ANTHROPIC_API_KEY=sk-ant-...
GITHUB_TOKEN=ghp_...
```

`AGENT_AGENT_ENV` controls which `.env.*` file loads (default: `dev`). Never commit secret values.

## Run

```bash
AGENT_AGENT_ENV=dev uvicorn agent_agent.server:app --reload --port 8100
```

## Verify

```bash
pytest tests/
mypy src/agent_agent/
ruff check src/ tests/
```
