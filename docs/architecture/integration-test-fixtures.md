# Integration Test Fixture Architecture

## 1. Problem Framing

The existing two-tier test infrastructure (unit tests against `:memory:` SQLite; component tests against local bare repos) cannot validate whether the orchestrator resolves real GitHub issues. This document describes a Tier 3 integration test system that spins up ephemeral live GitHub repos, plants real issues in them, runs the orchestrator end-to-end, and tears the repos down — all within pytest's fixture lifecycle.

This implements the "Tier 3 — Real-Repo End-to-End" testing described in `mvp-architecture.md §15`.

---

## 2. Relationship to Existing Fixtures

The codebase has two fixture patterns this design extends without replacing:

**`repo_with_remote(fixture_name)` in `tests/component/conftest.py`** — creates a local git repo + bare remote from a vendored snapshot. Function-scoped, purely local, no GitHub API.

**`github_test_repo` in `tests/fixtures/conftest.py`** — creates a real GitHub repo + issue under the user's personal account using `GITHUB_TOKEN`. Session-scoped, generic; does not plant real codebase content.

The integration fixture system is distinct from both:
- Uses `GITHUB_TOKEN` (same token for both bot operations and agent operations)
- Creates public repos directly under the bot account — no org required
- Repo names include a session-ID prefix (unique per pytest session) to support concurrent runs
- Clones real upstream repos at pinned SHAs, pushes with single-commit history, plants the specific issue from the candidate metadata
- Parametrized over the fixture catalog (per-repo JSON files)

---

## 3. File Layout

```
tests/
  conftest.py                          # (existing) root conftest — unchanged
  fixtures/
    __init__.py                        # (existing)
    conftest.py                        # (existing) github_test_repo — unchanged
    schedule.json                      # approved fixture catalog — one file per source repo
    <slug>.json                        # ...
  integration/                         # NEW
    __init__.py
    conftest.py                        # session_id, fixture_repo fixture, marker registration
    test_data/
      schema.json                      # test data for fixture generator tests (not agent eval)
    test_fixture_lifecycle.py          # tests the create/teardown machinery using test_data/
    test_issue_resolution.py           # agent eval tests — reads from tests/fixtures/ catalog
```

### Two distinct fixture catalogs

| Location | Purpose |
|----------|---------|
| `tests/fixtures/<slug>.json` | Approved catalog for agent evaluation (`test_issue_resolution.py`) |
| `tests/integration/test_data/schema.json` | Test data for validating the fixture generator itself (`test_fixture_lifecycle.py`) |

`test_data/schema.json` is committed source code (test infrastructure, not evaluation data). It is intentionally separate so that adding/removing repos from the main evaluation catalog does not affect the infrastructure unit tests, and vice versa.

No changes to `src/agent_agent/`. The GitHub repo lifecycle is test infrastructure only.

---

## 4. Fixture Metadata Format and Location

### Per-repo JSON files

Candidate metadata lives at `tests/fixtures/<repo-slug>.json`, one file per source repo. The slug is the repo name only (e.g. `schema` for `keleshev/schema`, `schedule` for `dbader/schedule`).

Each file is a JSON array of fixture records:

```json
[
  {
    "fixture_id": "schema-and-or-type-annotation",
    "complexity": "easy",
    "upstream": "https://github.com/keleshev/schema",
    "base_sha": "<40-char SHA>",
    "license": "MIT",
    "pr_number": 343,
    "issue_number": 342,
    "issue_title": "...",
    "issue_body": "...",
    "synthetic_issue": false,
    "merged_from": []
  }
]
```

Rationale for per-repo files: each target repo is independently researched and approved; per-repo files make it easy to add, review, or remove an entire source repo without touching others.

### Discovery

A loader `load_all_fixtures() -> list[FixtureMeta]` reads all `*.json` files in `tests/fixtures/`, returning a flat list. Called at module import time in `tests/integration/conftest.py` to build the parametrize list.

`FixtureMeta` is a Pydantic v2 model in `tests/integration/conftest.py`:

```python
class FixtureMeta(BaseModel):
    fixture_id: str
    complexity: Literal["easy", "medium", "hard"]
    upstream: str
    base_sha: str
    license: str
    pr_number: int
    issue_number: int
    issue_title: str
    issue_body: str
    synthetic_issue: bool
    merged_from: list[str]
```

---

## 5. Session ID

A short session ID is generated once per pytest session and prefixed to every ephemeral repo name. This ensures:
- No collision between concurrent test sessions (CI matrix, parallel developer runs)
- Stale repos from a crashed session are identifiable and cleanable

```python
# tests/integration/conftest.py — session-scoped, autouse=False
@pytest.fixture(scope="session")
def session_id() -> str:
    return uuid.uuid4().hex[:8]
```

Repo names take the form `aaf-<session_id>-<fixture_id>`, e.g. `aaf-a1b2c3d4-schema-and-or-type-annotation`. The `aaf-` prefix namespaces all ephemeral repos for easy visual identification and bulk cleanup.

GitHub repo name max length is 100 characters. At `4 + 8 + 1 + 35 = 48` characters max, this is well within limits.

A session-scoped finalizer (registered in a session-scoped autouse fixture) attempts to delete all repos matching `aaf-<session_id>-*` on the bot account at session end. This is a safety net — per-test teardown is the primary mechanism, but the session finalizer catches any repos that escaped teardown (e.g. killed test runner).

---

## 6. The `fixture_repo` Pytest Fixture

### Scope and placement

Defined in `tests/integration/conftest.py`. **Function-scoped** — each test gets a fresh ephemeral repo.

### Marker and skip logic

A custom marker `integration` is registered in `pyproject.toml` and `pytest_configure`. All integration tests carry `@pytest.mark.integration`.

A session-scoped autouse fixture checks for `GITHUB_TOKEN` at collection time. If absent, the session is skipped before any fixture setup runs.

One token plus claude CLI credentials are required end-to-end:

| Variable | Used by | Purpose |
|---|---|---|
| `GITHUB_TOKEN` | `_FixtureBotClient` and `GitHubClient` | Create/delete repos on the bot account; read repo, create branches, open PRs |

The SDK uses claude CLI credentials (`~/.claude/`), not `ANTHROPIC_API_KEY`. Ensure the
`claude` CLI is authenticated with a Max plan account before running SDK tests.

Missing `GITHUB_TOKEN` → skip (fixture infrastructure unavailable).
Missing claude CLI auth → test fails (agent execution failed). This distinction is intentional.

### Parametrize strategy

```python
@pytest.mark.integration
@pytest.mark.parametrize(
    "fixture_repo",
    load_all_fixtures(),
    indirect=True,
    ids=lambda m: m.fixture_id,
)
def test_resolves_issue(fixture_repo):
    repo_url, issue_number = fixture_repo
    ...
```

`ids=lambda m: m.fixture_id` enables `-k schema-and-or-type-annotation` to select a single fixture.

### Fixture signature

```python
@pytest.fixture()
def fixture_repo(
    request: pytest.FixtureRequest,
    session_id: str,
    tmp_path: Path,
) -> Generator[tuple[str, int], None, None]:
    meta: FixtureMeta = request.param
    repo_name = f"aaf-{session_id}-{meta.fixture_id}"
    ...
    yield repo_url, issue_number
    ...  # teardown via request.addfinalizer
```

Yields `(repo_url, issue_number)`. The full `FixtureMeta` is available via `request.param`.

---

## 7. GitHub Repo Lifecycle

All API calls use a synchronous `_FixtureBotClient` (thin `httpx.Client` wrapper with bot token headers). Not the production `GitHubClient`.

Repos are created as **public** repositories directly under the bot account — no org.

### Step 1: Create the repo

```
POST /user/repos
{
  "name": "aaf-<session_id>-<fixture_id>",
  "private": false,
  "auto_init": false,
  "description": "Ephemeral test fixture: <fixture_id>"
}
```

`auto_init: false` because content is pushed manually in step 2.

### Step 2: Clone, squash history, push

Run as subprocess calls from `tmp_path` (pytest manages cleanup):

1. `git clone <upstream> <tmp_path>` — full clone of upstream
2. `git checkout <base_sha>` — move to pre-fix state
3. Delete `.git/`, re-init as orphan repo — strips all history
4. `git add . && git commit -m "fixture: initial state"`
5. Push to `https://x-access-token:<BOT_TOKEN>@github.com/<bot_username>/aaf-<session_id>-<fixture_id>`

Git author identity set via `GIT_AUTHOR_NAME=fixture-bot` / `GIT_AUTHOR_EMAIL=fixture-bot@localhost` in subprocess env, following the same pattern as `tests/component/conftest.py`.

**Note on shallow clones:** Full clone used for correctness — GitHub doesn't reliably support shallow fetch by arbitrary SHA. For the current fixture repos (all small Python libraries < 5 MB) this is fast.

### Step 3: Post the issue

```
POST /repos/<bot_username>/aaf-<session_id>-<fixture_id>/issues
{ "title": "<issue_title>", "body": "<issue_body>" }
```

Capture `issue_number` from the response — do not hardcode `1`.

### Yield

```python
yield f"https://github.com/<bot_username>/aaf-{session_id}-{fixture_id}", issue_number
```

### Step 4 (teardown): Delete the repo

Registered as a `request.addfinalizer`:

```python
def teardown():
    try:
        bot_client.delete_repo(f"<bot_username>/aaf-{session_id}-{fixture_id}")
    except Exception as e:
        warnings.warn(f"Fixture teardown failed for {fixture_id}: {e}")

request.addfinalizer(teardown)
```

Teardown failures log a warning and do not re-raise.

### Session-level safety net

A session-scoped autouse fixture lists all repos on the bot account matching `aaf-<session_id>-` at session end and deletes any that still exist (escaped per-test teardown):

```python
@pytest.fixture(scope="session", autouse=True)
def _session_cleanup(session_id, request):
    yield
    # list repos matching aaf-<session_id>- and delete each
    ...
```

---

## 8. `_FixtureBotClient`

Synchronous `httpx.Client` wrapper. Context-manager. Lives in `tests/integration/conftest.py`.

Methods:
- `create_repo(repo_name: str, description: str) -> str` — `POST /user/repos`; returns full repo name `<username>/<repo_name>`
- `post_issue(repo_name: str, title: str, body: str) -> int` — returns issue number
- `delete_repo(repo_name: str) -> None` — logs warning on failure, does not raise
- `list_repos_with_prefix(prefix: str) -> list[str]` — used by session cleanup to find `aaf-<session_id>-*` repos

---

## 9. Integration with `pyproject.toml`

```toml
[tool.pytest.ini_options]
markers = [
    "github: tests requiring GITHUB_TOKEN",
    "sdk: tests requiring claude CLI auth (Max plan; unset CLAUDECODE first)",
    "integration: end-to-end tests requiring GITHUB_TOKEN + claude CLI auth",
]
addopts = "-m 'not integration'"
```

Running integration tests explicitly:

```bash
pytest tests/integration/ -m integration -v
pytest tests/integration/ -k schema-and-or-type-annotation -v
```

---

## 10. Per-Complexity Timeouts

| Complexity | Timeout |
|---|---|
| easy | 180 s |
| medium | 420 s |
| hard | 900 s |

Tests read `request.param.complexity` and set the timeout dynamically.

---

## 11. One-Time Setup

Before the first integration test run:
1. Create a dedicated GitHub bot account (e.g. `agent-agent-fixture-bot`)
2. Generate a PAT with `repo` and `delete_repo` scopes
3. Store as `GITHUB_TOKEN` in `.env`

No org required. All ephemeral repos are created under the bot account directly.

---

## 12. Implementation Sequence

1. Register `integration` marker in `pyproject.toml`; add `addopts` exclusion
2. `tests/integration/test_data/schema.json` is already in place — use it to drive `test_fixture_lifecycle.py`
3. Create `tests/integration/__init__.py` and skeleton `conftest.py`
4. Implement `FixtureMeta` model and `load_all_fixtures()` loader
5. Implement `_FixtureBotClient`
6. Implement `session_id` fixture and session cleanup fixture
7. Implement `fixture_repo` fixture with full lifecycle
8. Write `test_fixture_lifecycle.py` — validates create/push/issue/teardown against a single entry from `test_data/schema.json`
9. Write `test_issue_resolution.py` with a single easy fixture from the main catalog; validate end-to-end
10. Expand to remaining fixtures once the easy path is stable

---

## 13. Open Questions

- **Bot account username:** The bot account name (used in repo URLs) must be known at implementation time. It is read from the GitHub API (`GET /user`) using `GITHUB_TOKEN` at the start of the session — not hardcoded.
- **Shallow clone:** Full clone used for correctness. If fixture repos grow large, switch to `shutil.copytree` from a vendored snapshot (see §7 Step 2).
- **Stale repos from very old sessions:** The `aaf-` prefix makes it easy to bulk-delete stale repos via `gh repo list <bot_username> --json name | jq '.[] | select(.name | startswith("aaf-"))' | xargs gh repo delete`. Document this as a periodic maintenance command.
- **Synthetic issues:** `merged_from` is metadata only; no special handling needed in the fixture machinery.
