# Dev/Prod Separation

## Problem Statement

The orchestrator needs to behave differently in development (verbose logging, relaxed budgets, local git, mock-friendly) and production (structured logging, strict budgets, remote git, hardened). These environments must be switchable without code changes, and the separation must be clean enough that a dev configuration can never accidentally affect production systems (pushing to a production repo, posting on real issues, spending real budget with loose limits).

## State of the Art

### 12-Factor App Configuration
The twelve-factor methodology (Heroku, 2012) established the standard: store config in environment variables, not in code. The app reads `DATABASE_URL`, `API_KEY`, etc. from the environment. Different environments are created by setting different env vars, not by maintaining separate codebases or config files.

**Key insight:** Config that varies between deploys (dev/staging/prod) belongs in the environment, not in the code. But the code must define sensible defaults and validate that required config is present.

### Python pydantic-settings
`pydantic-settings` extends Pydantic's validation to environment variables and `.env` files. It provides:

- Type-safe config with validation
- Automatic loading from env vars and `.env` files
- Nested settings with prefix scoping
- Default values with override capability

```python
class Settings(BaseSettings):
    env: str = "dev"
    anthropic_api_key: str
    github_token: str
    log_level: str = "DEBUG"
    max_budget_tokens: int = 100_000

    model_config = SettingsConfigDict(
        env_file=f".env.{os.getenv('AGENT_AGENT_ENV', 'dev')}",
        env_prefix="AGENT_AGENT_",
    )
```

This is the recommended approach for Python projects. It's the standard in FastAPI applications.

### Docker Compose Profiles
Docker Compose supports profiles that activate different service configurations:

```yaml
services:
  app:
    profiles: [dev, prod]
    environment:
      - AGENT_AGENT_ENV=${PROFILE}
  debug-tools:
    profiles: [dev]
```

For the future containerized version, this provides environment-level separation at the infrastructure layer.

### GitHub Environments
GitHub Actions supports named environments (dev, staging, production) with:

- Environment-specific secrets
- Required reviewers before deployment
- Wait timers
- Branch restrictions

This maps to future CI/CD integration where the orchestrator runs in different GitHub environments.

### Feature Flags (LaunchDarkly, Unleash, Flipt)
Feature flag systems allow toggling behavior at runtime without redeployment. For an orchestrator, flags could control: which agent models to use, budget limits, checkpoint levels, experimental features. Overkill for MVP but relevant for multi-user production.

## Best Practices

### 1. Environment File Structure

```
repos/agent_agent/
├── .env.dev            # Dev defaults (committed, no secrets)
├── .env.prod           # Prod defaults (committed, no secrets)
├── .env.local          # Local overrides (gitignored, has secrets)
├── .env.dev.example    # Example dev config (committed)
├── .env.prod.example   # Example prod config (committed)
```

Loading order:
1. `.env.{AGENT_AGENT_ENV}` — environment-specific defaults
2. `.env.local` — local overrides (API keys, personal preferences)
3. Actual environment variables — highest priority

### 2. Config Differences Between Dev and Prod

| Setting | Dev | Prod |
|---|---|---|
| `LOG_LEVEL` | `DEBUG` | `INFO` |
| `LOG_FORMAT` | `console` (human-readable) | `json` (machine-parseable) |
| `MAX_BUDGET_TOKENS` | `500_000` (generous) | `100_000` (strict) |
| `MAX_RETRIES` | `5` (tolerant) | `2` (fail fast) |
| `CHECKPOINT_LEVEL` | `minimal` | `standard` |
| `GIT_PUSH_ENABLED` | `false` | `true` |
| `DRY_RUN_GITHUB` | `true` | `false` |
| `MODEL` | `claude-haiku-4-5-20251001` (cheaper) | `claude-sonnet-4-6` |
| `DB_PATH` | `data/dev.db` | `data/prod.db` |

### 3. Safety Rails for Dev

Dev mode should make destructive actions hard to do accidentally:

```python
class DevSafetyMiddleware:
    async def __call__(self, request, call_next):
        if settings.env == "dev":
            # Prevent accidental pushes to real repos
            if settings.git_push_enabled is False:
                # Intercept and log git push commands instead of executing
                pass
            # Prevent posting to real GitHub issues
            if settings.dry_run_github:
                # Log the API call that would have been made
                pass
        return await call_next(request)
```

### 4. Separate Databases Per Environment

Never share a database between dev and prod:

```python
DB_PATHS = {
    "dev": "data/dev.db",
    "prod": "data/prod.db",
    "test": ":memory:",  # In-memory for tests
}
```

This prevents dev experiments from corrupting production state and vice versa.

### 5. Secret Management

Secrets (`GITHUB_TOKEN`) should never be in committed files:

- In `.env.local` (gitignored) for local development
- In environment variables for CI/CD
- In a secret manager (AWS SSM, Vault, 1Password CLI) for production

> **SDK auth uses claude CLI credentials, not `ANTHROPIC_API_KEY`.** The SDK spawns a
> `claude` subprocess authenticated via `~/.claude/`. Do not add `ANTHROPIC_API_KEY` to
> secret stores for this project — if set, it overrides Max plan auth.

The committed `.env.dev` and `.env.prod` files contain only non-sensitive defaults.

### 6. Git Branch Protection

For the future production version:

```python
PROTECTED_BRANCHES = {"main", "master", "production"}

async def validate_branch_operation(branch: str, operation: str):
    if branch in PROTECTED_BRANCHES and operation in ("push", "force-push", "delete"):
        raise PermissionError(
            f"Cannot {operation} to protected branch '{branch}'. "
            f"Agents must work on feature branches."
        )
```

### 7. Single Entry Point, Behavior Controlled by Config

Don't maintain separate `server_dev.py` and `server_prod.py`. One codebase, one entry point:

```bash
# Dev
AGENT_AGENT_ENV=dev uvicorn agent_agent.server:app --reload --port 8100

# Prod
AGENT_AGENT_ENV=prod uvicorn agent_agent.server:app --workers 4 --port 8100
```

The `--reload` flag and `--workers` count are the only things that change at the command level.

## Previous Stable Approach

### Separate Codebases / Branches
Maintaining a `dev` branch and a `prod` branch with different config hardcoded. Changes are cherry-picked between branches. This diverges quickly and is a maintenance nightmare. Universally abandoned in favor of environment-based configuration.

### Config Files with Inheritance
`config.base.yaml` + `config.dev.yaml` + `config.prod.yaml` with deep merge. Common in Java/Spring applications. Works but adds complexity — developers must understand the merge order and precedence rules. Environment variables are simpler and more standard.

### Conditional Imports
```python
if os.getenv("ENV") == "prod":
    from config_prod import *
else:
    from config_dev import *
```

Fragile, hard to test, and violates the principle that code shouldn't change between environments.

### Build-Time Configuration
Bake configuration into the build artifact (Docker image, compiled binary). Different environments get different builds. This works for immutable infrastructure but prevents runtime configuration changes and makes debugging harder (the artifact you're debugging is different from the one in production).

### Manual Configuration
No configuration framework — each deployment is manually configured by editing files on the server. Works for a single developer on a single machine (which is the MVP scenario) but doesn't scale and provides no safety rails against misconfiguration.
