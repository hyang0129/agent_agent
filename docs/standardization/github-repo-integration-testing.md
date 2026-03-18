# GitHub Repo Integration Testing — Standard

This document defines the standard approach for Tier 3 integration tests in agent_agent: how
ephemeral GitHub repos are created per-test, how the fixture catalog is maintained, and how
to add new fixtures. All contributors writing integration tests or adding evaluation fixtures
must follow this standard.

For the full architectural rationale see
[`docs/architecture/integration-test-fixtures.md`](../architecture/integration-test-fixtures.md).

---

## 1. What Was Built

The integration test system spins up a real GitHub repo for each test, plants a real issue in
it, runs the agent end-to-end, and tears the repo down — all within pytest's fixture lifecycle.

### Core components

| Component | Location | Purpose |
|-----------|----------|---------|
| `FixtureMeta` | `tests/integration/conftest.py` | Pydantic model for a fixture catalog entry |
| `load_all_fixtures()` | `tests/integration/conftest.py` | Reads all `*.json` from a catalog directory |
| `_FixtureBotClient` | `tests/integration/conftest.py` | Synchronous httpx wrapper for bot account ops |
| `session_id` fixture | `tests/integration/conftest.py` | 8-char hex prefix, unique per pytest session |
| `_session_cleanup` fixture | `tests/integration/conftest.py` | Session-level safety net for stale repo deletion |
| `fixture_repo` fixture | `tests/integration/conftest.py` | Creates ephemeral repo, yields `(repo_url, issue_number)` |
| `test_fixture_lifecycle.py` | `tests/integration/` | Tests the create/push/issue/teardown machinery |
| `test_issue_resolution.py` | `tests/integration/` | Agent eval tests (stub until catalog is populated) |

### Two fixture catalogs

| Catalog | Location | Purpose |
|---------|----------|---------|
| Generator test data | `tests/integration/test_data/<slug>.json` | Validates the fixture machinery itself; committed source code |
| Agent eval catalog | `tests/fixtures/<slug>.json` | Approved fixtures for agent evaluation; one file per source repo |

These catalogs are intentionally separate. Changes to the eval catalog do not affect
infrastructure tests, and vice versa.

---

## 2. Required Environment Variables

| Variable | Used by | Absence behavior |
|----------|---------|-----------------|
| `AGENT_AGENT_FIXTURE_BOT_TOKEN` | `_FixtureBotClient` — creates/deletes repos on the bot account | Tests are **skipped** |
| `GITHUB_TOKEN` | Agent under test — reads repos, creates branches, opens PRs | Tests **fail** |
| `ANTHROPIC_API_KEY` | Agent SDK — runs Claude agents | Tests **fail** |

Store all three in `.env` at the repo root. Load with `set -a && source .env && set +a`.

The distinction is intentional: a missing bot token means fixture infrastructure is
unavailable (skip gracefully); missing agent credentials means the agent failed to run
(hard failure).

---

## 3. Running Integration Tests

Integration tests are excluded from the default `pytest` run via `addopts = "-m 'not integration'"`.
To run them explicitly:

```bash
# All integration tests
pytest tests/integration/ -m integration -v

# Fixture lifecycle only (infrastructure self-test)
pytest tests/integration/test_fixture_lifecycle.py -m integration -v

# A single fixture by ID
pytest tests/integration/ -m integration -k schema-and-or-type-annotation -v
```

Integration tests create real GitHub repos. Expect 30–900 s per test depending on complexity:

| Complexity | Timeout |
|------------|---------|
| easy | 180 s |
| medium | 420 s |
| hard | 900 s |

---

## 4. The `fixture_repo` Pytest Fixture

`fixture_repo` is **function-scoped** and must be used with `indirect=True`:

```python
@pytest.mark.integration
@pytest.mark.parametrize(
    "fixture_repo",
    load_all_fixtures(),   # or load_all_fixtures(Path(...) / "test_data")
    indirect=True,
    ids=lambda m: m.fixture_id,
)
def test_resolves_issue(fixture_repo: tuple[str, int]) -> None:
    repo_url, issue_number = fixture_repo
    # repo_url  — "https://github.com/<bot>/<repo-name>"
    # issue_number — the GitHub issue number posted in the ephemeral repo
```

What `fixture_repo` does per test:
1. Creates a public repo on the bot account: `aaf-<session_id>-<fixture_id>`
2. Clones the upstream at `base_sha`, squashes history to a single commit, pushes to the new repo
3. Posts the issue from `FixtureMeta` and captures the `issue_number`
4. Registers a finalizer that deletes the repo on teardown
5. Yields `(repo_url, issue_number)`

The session-level `_session_cleanup` fixture additionally deletes any repos matching
`aaf-<session_id>-*` that escaped per-test teardown (e.g. killed test runner).

---

## 5. Fixture Catalog Format

Each catalog file is a JSON array of objects conforming to `FixtureMeta`:

```json
[
  {
    "fixture_id": "schema-and-or-type-annotation",
    "complexity": "easy",
    "upstream": "https://github.com/keleshev/schema",
    "base_sha": "<40-char SHA — commit BEFORE the fix was merged>",
    "license": "MIT",
    "pr_number": 343,
    "issue_number": 342,
    "issue_title": "Wrong type annotation for `And` and `Or`",
    "issue_body": "<verbatim GitHub issue body>",
    "synthetic_issue": false,
    "merged_from": []
  }
]
```

| Field | Rules |
|-------|-------|
| `fixture_id` | `<repo-slug>-<2-4-word-slug>`, kebab-case, < 35 chars, unique across the catalog |
| `base_sha` | 40-char hex; the parent commit of the fix PR — the state BEFORE the bug was fixed |
| `issue_body` | Verbatim from GitHub, never paraphrased; or a synthetic body if `synthetic_issue: true` |
| `merged_from` | Empty list normally; `[N, M]` (ints) if two related issues were merged into one synthetic issue |

Files are named after the source repo slug: `schema.json` for `keleshev/schema`.

---

## 6. Adding New Fixtures to the Eval Catalog

Follow the 3-step runbook in
[`scripts/fixtures/repo_fixture_creation/README.md`](../../scripts/fixtures/repo_fixture_creation/README.md):

**Step 1 — Verify eligibility (shell script)**
```bash
TARGET_REPO=owner/repo \
AGENT_AGENT_FIXTURE_BOT_TOKEN=$AGENT_AGENT_FIXTURE_BOT_TOKEN \
  ./scripts/fixtures/repo_fixture_creation/01_verify_eligibility.sh
```
Checks: permissive license, Python-only language, no vendored dependency trees.

**Step 2 — Repo health check (agent op)**
Run `scripts/fixtures/repo_fixture_creation/prompts/02_repo_health_prompt.md` with
`{{TARGET_REPO}}` substituted. Must return `"verdict": "pass"`.

**Step 3 — PR research (agent op)**
Run `scripts/fixtures/repo_fixture_creation/prompts/01_pr_research_prompt.md` with
`{{TARGET_REPO}}` substituted. Produces `staging/<repo-slug>.json` with 5 entries
(3 easy, 1 medium, 1 hard).

The `staging/` directory is **gitignored**. Candidates live there until human review.

**Promotion**: after reviewing the staging file, copy it to `tests/fixtures/<slug>.json`
and commit. Once committed, the fixtures become active in `test_issue_resolution.py`.

---

## 7. Naming Conventions

| Thing | Convention | Example |
|-------|-----------|---------|
| Ephemeral repo name | `aaf-<session_id>-<fixture_id>` | `aaf-a1b2c3d4-schema-and-or-type-annotation` |
| Catalog file | `<repo-slug>.json` | `schema.json` for `keleshev/schema` |
| `fixture_id` | `<repo-slug>-<short-description>` | `schema-and-or-type-annotation` |
| `aaf-` prefix | Namespaces all ephemeral repos for easy visual identification and bulk cleanup | — |

GitHub repo name max is 100 chars. At `4 + 8 + 1 + 35 = 48` chars max, all names are well within limits.

To bulk-delete stale repos from crashed sessions:
```bash
gh repo list <bot_username> --json name \
  | jq -r '.[] | select(.name | startswith("aaf-")) | .name' \
  | xargs -I{} gh repo delete <bot_username>/{} --yes
```

---

## 8. One-Time Bot Account Setup

Before running any integration tests:

1. Create a dedicated GitHub bot account (e.g. `agent-agent-fixture-bot`)
2. Generate a PAT with `repo` and `delete_repo` scopes
3. Add to `.env`: `AGENT_AGENT_FIXTURE_BOT_TOKEN=<token>`

No org is required. All ephemeral repos are created under the bot account directly as public
repos.
