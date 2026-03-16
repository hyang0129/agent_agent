# Integration Testing — Principles & Architecture

## Problem Statement

Agent Agent is an orchestrator that resolves GitHub issues autonomously. Unit tests can verify DAG construction, state persistence, and context passing in isolation — but they cannot answer the question that matters: "Given a real issue on a real codebase, does the orchestrator produce a correct, mergeable PR?"

The only way to validate end-to-end behavior is to run the orchestrator against real repositories with real issues and evaluate the results against known-correct solutions.

## Approach

We maintain three purpose-built test repositories on GitHub. Each repo is a realistic codebase with a curated set of issues. Each issue has:

- A clear description (as a real user would write it)
- A known-correct solution (stored separately, never visible to the orchestrator)
- A difficulty rating and expected agent decomposition
- Acceptance criteria that can be checked programmatically (tests pass, lint passes, specific behavior verified)

The orchestrator is pointed at an issue. It produces a PR. The PR is evaluated against the acceptance criteria. This is the integration test.

## Test Repositories

### 1. test-webstore — Shopify-style E-Commerce (Python)

**Stack:** FastAPI, SQLAlchemy, Pydantic, Jinja2 templates, Stripe-like payment stubs, PostgreSQL (via SQLite for tests)

**Domain complexity:** Product catalog, shopping cart, checkout flow, order management, user accounts, inventory tracking, discount codes, webhook handlers.

**Why this domain:**
- CRUD-heavy with relational data models — tests the orchestrator's ability to trace data flow across models, routes, and templates
- Business logic with edge cases (out-of-stock during checkout, expired discount codes, partial refunds) — tests whether agents correctly handle conditional logic
- Multiple integration points (payment, email notifications, inventory) — tests the planner's ability to identify affected components

**Example issue tiers:**

| Tier | Example Issue | Tests What |
|---|---|---|
| Simple | "Add a `created_at` timestamp to the Order model and display it on the order detail page" | Single-file model change + template update. Verifies basic research → implement → test flow |
| Medium | "Implement discount code support: model, validation at checkout, and display on receipt" | Multi-file feature spanning models, routes, templates, and tests. Verifies DAG decomposition into parallel subtasks |
| Complex | "Cart items should reserve inventory for 15 minutes. If checkout isn't completed, release the reservation" | Async behavior, race conditions, background task. Verifies the planner's ability to identify non-obvious architectural concerns |

### 2. test-mailservice — Gmail-style Email Service (Python)

**Stack:** FastAPI, async task queue (arq/celery-like), full-text search (SQLite FTS5 or Whoosh), OAuth2 stubs, WebSocket for real-time updates

**Domain complexity:** Compose/send/receive, threading and conversations, labels/folders, search, drafts, attachments (metadata only), spam filtering stubs, contact management.

**Why this domain:**
- Async processing pipeline (receive → process → index → notify) — tests agent understanding of async architectures and event-driven systems
- Search indexing as a cross-cutting concern — tests whether agents recognize that changes to the email model must propagate to the search index
- Auth and multi-tenancy — tests that agents respect isolation boundaries and don't introduce security regressions

**Example issue tiers:**

| Tier | Example Issue | Tests What |
|---|---|---|
| Simple | "Add a 'starred' boolean to emails and a filter to show only starred messages" | Model + query + API endpoint. Baseline competence check |
| Medium | "Implement email threading: group replies by conversation, display as expandable thread in the API response" | Data model redesign, query changes, API restructuring. Tests the planner's ability to recognize a schema migration |
| Complex | "Add full-text search across email body, subject, and sender with ranking and snippet highlighting" | Cross-cutting feature touching ingestion pipeline, search index, and query API. Tests multi-agent coordination |

### 3. test-agent-agent — Previous Version of Itself (Python)

**Stack:** FastAPI, Anthropic SDK, NetworkX, SQLite, Pydantic — an earlier, simpler version of agent_agent itself.

**Domain complexity:** DAG construction, agent dispatch, state management, GitHub integration, configuration — the same domain as the orchestrator being tested.

**Why this domain:**
- Self-referential reasoning — the orchestrator must understand its own architecture to modify a system like itself. This is the hardest test of architectural comprehension
- Meta-cognitive challenge — an agent reasoning about agent orchestration exposes whether the system has genuine understanding or is pattern-matching
- Catches architectural blind spots — if the orchestrator can't improve its own predecessor, it likely can't handle novel architectures either

**Example issue tiers:**

| Tier | Example Issue | Tests What |
|---|---|---|
| Simple | "Add a `/health` endpoint that returns the current DAG execution count and uptime" | Trivial FastAPI addition. Sanity check |
| Medium | "Implement per-node token budget tracking that halts execution when a node exceeds its allocation" | Touches executor, state store, and config. Tests understanding of the orchestration flow |
| Complex | "Add support for conditional edges in the DAG — nodes that only execute if a predecessor's output meets a specified condition" | Core architecture change to the DAG engine. Tests deep comprehension of graph execution semantics |

## Evaluation Criteria

Each test issue is evaluated on multiple dimensions:

### 1. Correctness
- Do the existing tests still pass? (no regressions)
- Do the new/modified tests pass?
- Does the implementation match the issue requirements?

### 2. Completeness
- Were all aspects of the issue addressed?
- Were necessary migrations, config changes, or documentation updates included?
- Were edge cases handled?

### 3. Code Quality
- Does the code follow the repo's existing patterns and style?
- Is the solution appropriately scoped (not over-engineered)?
- Are there no introduced security vulnerabilities?

### 4. Process Quality
- Was the DAG decomposition reasonable? (not too granular, not too coarse)
- Did agents stay within their permission boundaries?
- Was the budget usage proportional to the task complexity?
- Were context handoffs between agents effective?

### 5. PR Quality
- Is the PR description clear and accurate?
- Are the commits logically organized?
- Would a human reviewer understand the changes without reading agent logs?

## Test Execution Architecture

```
┌──────────────────────────────────────────────────┐
│                  Test Runner                      │
│                                                   │
│  for each (repo, issue) in test_matrix:           │
│    1. Reset test repo to known state (git reset)  │
│    2. POST /api/v1/tasks {issue_url}              │
│    3. Poll until DAG completes or times out       │
│    4. Fetch resulting PR                          │
│    5. Run evaluation suite against PR diff         │
│    6. Record results + metrics                    │
│                                                   │
│  ┌─────────────────────────────────────────────┐  │
│  │           Evaluation Suite                   │  │
│  │                                              │  │
│  │  ┌────────────┐  ┌────────────┐             │  │
│  │  │  Checkout   │  │  Run repo  │             │  │
│  │  │  PR branch  │  │  tests     │             │  │
│  │  └─────┬──────┘  └─────┬──────┘             │  │
│  │        │               │                     │  │
│  │        ▼               ▼                     │  │
│  │  ┌────────────┐  ┌────────────┐             │  │
│  │  │  Diff vs   │  │  Lint /    │             │  │
│  │  │  expected   │  │  typecheck │             │  │
│  │  └─────┬──────┘  └─────┬──────┘             │  │
│  │        │               │                     │  │
│  │        ▼               ▼                     │  │
│  │  ┌──────────────────────────┐               │  │
│  │  │    Score & Report        │               │  │
│  │  └──────────────────────────┘               │  │
│  └─────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────┘
```

### Reset Strategy

Each test run starts from a deterministic state:

```bash
cd test-repo
git checkout main
git reset --hard <known-commit-sha>
git clean -fd
# Close any open PRs/branches from previous runs
gh pr list --state open --json number -q '.[].number' | xargs -I{} gh pr close {}
```

The known commit SHA is pinned in the test configuration. The test repo is never modified manually — all changes come through the orchestrator or the reset script.

### Timeout and Budget Limits

Each test issue has configured limits:

```python
class TestCase(BaseModel):
    repo: str
    issue_number: int
    tier: Literal["simple", "medium", "complex"]
    timeout_seconds: int          # simple=120, medium=300, complex=600
    max_budget_tokens: int        # simple=50k, medium=150k, complex=500k
    expected_files_changed: list[str]   # For completeness check
    expected_test_count: int            # Minimum new/modified tests
```

### Scoring

Each dimension is scored 0–3:

| Score | Meaning |
|---|---|
| 0 | Not attempted or fundamentally broken |
| 1 | Partially addressed, significant issues |
| 2 | Mostly correct, minor issues |
| 3 | Fully correct, production-quality |

Automated scoring handles correctness (test results) and completeness (file coverage). Code quality and PR quality are scored by a review agent, with periodic human calibration.

## Test Matrix

The full test matrix runs all tiers across all repos:

```
          simple    medium    complex
webstore    ✓         ✓         ✓
mailservice ✓         ✓         ✓
agent-agent ✓         ✓         ✓
```

**9 test cases total.** Each run of the full matrix provides a comprehensive snapshot of orchestrator capability.

For development iteration, run only the simple tier (~3 minutes, ~$1.50 in API cost). The full matrix is for milestone validation (~30 minutes, ~$15).

## Regression Tracking

Test results are stored in a results database:

```sql
CREATE TABLE test_runs (
    id TEXT PRIMARY KEY,
    timestamp TIMESTAMP,
    agent_agent_commit TEXT,           -- Version of the orchestrator
    repo TEXT,
    issue_number INTEGER,
    tier TEXT,
    correctness_score INTEGER,
    completeness_score INTEGER,
    quality_score INTEGER,
    pr_score INTEGER,
    total_tokens INTEGER,
    duration_seconds INTEGER,
    passed BOOLEAN                     -- Overall pass/fail
);
```

This enables tracking orchestrator quality over time: "Did the latest planner change improve medium-tier scores?"

## Design Principles

### 1. Test Repos Are Real, Not Mocks
The test repositories are fully functional applications with real tests, real linting, and real CI. They are not toy examples. The issues are written as a real user would write them — sometimes vague, sometimes overly detailed, sometimes missing context. This is intentional.

### 2. Known Solutions Are Hidden
The known-correct solutions are stored outside the test repos (in agent_agent's test fixtures). The orchestrator has no access to them during execution. Solutions are only used for post-hoc evaluation, not as hints.

### 3. Issues Are Immutable Once Published
Once a test issue is created on GitHub, it is never edited. If we need a variant, we create a new issue. This ensures reproducibility — the orchestrator always sees the same issue text.

### 4. Test Repos Evolve Independently
Each test repo has its own maintainer cadence. When agent_agent improves, we may add harder issues to the test repos — but we never modify existing issues or their solutions. New issues are additive.

### 5. The Orchestrator Must Not Know It's Being Tested
No special test-mode flags. No "this is a test repo" hints in the configuration. The orchestrator treats test repos exactly as it would treat a real user's repository. If it needs special handling for tests, that's a design flaw.

### 6. Flaky Tests Are Bugs
If the same issue produces different quality results on repeated runs, that's a bug in the orchestrator (usually in the planner or context passing). LLM non-determinism is expected, but the orchestrator should be robust to it — multiple valid approaches are fine, but fundamentally different decompositions for the same issue indicate insufficient planning constraints.
