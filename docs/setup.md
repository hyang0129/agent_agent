# Setup

## Prerequisites

- Python 3.11
- A GitHub personal access token with `repo` scope
- Claude CLI authenticated with a Max plan account (`~/.claude/` credentials)

### Install the Claude CLI

```bash
curl -fsSL https://claude.ai/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"  # add to ~/.bashrc to persist
```

Then authenticate:

```bash
claude login
```

Verify auth:

```bash
claude auth status
```

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
GITHUB_TOKEN=ghp_...
```

> **SDK auth uses claude CLI credentials, not an API key.** The SDK spawns a `claude`
> subprocess authenticated via `~/.claude/`. Do not set `ANTHROPIC_API_KEY` — if present,
> it overrides Max plan auth and routes calls through the pay-per-token API.

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
