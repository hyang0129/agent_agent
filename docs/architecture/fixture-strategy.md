# Test Fixture Strategy — Real-Repo Snapshots

## Problem

The existing `tmp_git_repo` fixture is a synthetic 4-file Python project: one function, one test. It is adequate for unit and basic component tests (does the worktree manager create a branch? does the DAG executor call the right agents?) but it fails to exercise the behaviors that matter most at integration time:

- Can the research planner correctly map a real codebase with multiple modules and inter-file dependencies?
- Does the coding composite produce idiomatic changes that fit an existing style?
- Does the test designer write tests that actually exercise the regression, not just the happy path?
- Does the reviewer catch real issues — unused imports, missing edge cases, incorrect type annotations?

A 4-file repo answers none of these questions. We need real codebases.

## Selected Fixtures

Five repos are selected from the candidate list, covering a spread of sizes and issue archetypes.

---

### 1. `keleshev/schema` — ~600 LOC

**URL:** https://github.com/keleshev/schema
**Pinned SHA:** `a90e49bba3fb25f1e8a9bf0b6c6b6fd17ed4e793`
**CPU-only:** Yes — pure Python, no external dependencies.

**Best for:** Simple single-function addition, error message improvement.

**Example issue:**

> **Title:** `Schema.validate` should include the schema type in `SchemaError` messages
>
> Currently, when validation fails, the error message shows only the offending value: `"hello" should be instance of int`. Add the schema's own type to the message so callers see: `"hello" should be instance of int (schema: <class 'int'>)`. Update the existing error message tests to match the new format.

**Why better than the synthetic fixture:** The synthetic repo has one function and one test. `schema` has 15+ validator types, each producing a different error path. The agent must trace the shared `SchemaError` construction path, understand that changing the message format breaks existing tests (intentionally), and update both the source and the tests consistently. This is a real regression-aware change, not a trivial addition.

---

### 2. `dbader/schedule` — ~700 LOC

**URL:** https://github.com/dbader/schedule
**Pinned SHA:** `6a9e0b19f60f9e09ee55a3a7cd5f1fefed5f59e4`
**CPU-only:** Yes — pure Python, stdlib only.

**Best for:** Bug fix in a stateful class, edge case handling.

**Example issue:**

> **Title:** Jobs with `until()` deadline are not cancelled when deadline passes between `run_pending()` calls
>
> If a job has an `until()` deadline set and the scheduler is not called for a period longer than the job's interval, the job may run once after its deadline has already passed. Reproduce: set a job with a 1-second interval, a 2-second deadline, then call `run_pending()` at t=0 and t=3. The job fires at t=3 despite the deadline. Fix the pre-run deadline check so that jobs past their deadline are cancelled before execution.

**Why better than the synthetic fixture:** This is a behavioral bug in a class with non-trivial time-dependent state (`Job`, `Scheduler`, `CancelJob`). The agent must read the existing deadline implementation, write a regression test that actually demonstrates the bug, fix the run guard, and verify that the fix does not break the `until()` happy path. The synthetic fixture has no state.

---

### 3. `sloria/environs` — ~900 LOC

**URL:** https://github.com/sloria/environs
**Pinned SHA:** `c1d6a9c3fc7e87c2a7d0dfb46d8cef7a02a3ef56`
**CPU-only:** Yes — pure Python, marshmallow dependency only.

**Best for:** Multi-file feature addition, type annotation coverage.

**Example issue:**

> **Title:** Add `Env.dump()` method that serializes all parsed variables back to a dict
>
> `Env` currently only reads environment variables. Add a `dump()` method that returns a `dict[str, Any]` of all variables that were parsed during the current session, using the same field names defined in the schema. This mirrors marshmallow's `Schema.dump()` semantics. Add tests for the default case and for the case where a prefix was set on the `Env` instance.

**Why better than the synthetic fixture:** `environs` wraps marshmallow's schema machinery. Adding `dump()` requires the agent to understand how `Env` tracks parsed fields (through `_fields`), how prefixes affect key names, and how to delegate to marshmallow's serialization without re-implementing it. This is the simplest class of multi-file feature where the planner must correctly identify which existing components to reuse.

---

### 4. `jmespath/jmespath.py` — ~2,500 LOC

**URL:** https://github.com/jmespath/jmespath.py
**Pinned SHA:** `f97b1571b1017ad3bb1543d7e14f50e44f4aba7b`
**CPU-only:** Yes — pure Python, no C extensions.

**Best for:** Parser bug fix, multi-file refactor, cross-module tracing.

**Example issue:**

> **Title:** `search()` raises `TypeError` instead of `JMESPathError` when input is not JSON-serializable
>
> When `jmespath.search()` is called with a Python object that contains non-serializable values (e.g., a `datetime`), the underlying JSON normalization raises a bare `TypeError`. This leaks implementation details to callers who expect only `JMESPathError` subclasses. Wrap the normalization step so that `TypeError` and `ValueError` from the serializer are caught and re-raised as `JMESPathTypeError` with the original message preserved. Add a test case covering this path.

**Why better than the synthetic fixture:** The search path runs through `parser.py`, `lexer.py`, `visitor.py`, and the function registry. The agent must trace control flow across four modules to locate where normalization occurs, understand the exception hierarchy, and add a wrapping without introducing regressions in the 200+ existing compliance tests. The synthetic fixture has one module.

---

### 5. `python-attrs/cattrs` — ~4,000 LOC

**URL:** https://github.com/python-attrs/cattrs
**Pinned SHA:** `8c8f9a3e3f1c7b14a1c9a9e4e27b2e5c14c1a9b3`
**CPU-only:** Yes — pure Python, attrs/annotated-types only.

**Best for:** Complex multi-file refactor, hook/dispatch architecture, type-system interaction.

**Example issue:**

> **Title:** `ClassValidationError` should expose a `group_by_field()` helper that returns exceptions keyed by field name
>
> `ClassValidationError` aggregates structuring errors per field but provides no way to query them by field name without iterating `exceptions` manually. Add a `group_by_field() -> dict[str, list[Exception]]` method to `ClassValidationError`. Fields with no errors must not appear in the result. Add tests covering single-field, multi-field, and no-error cases. Update the changelog stub.

**Why better than the synthetic fixture:** `cattrs` uses a hook-dispatch architecture where structuring errors are assembled from multiple validator hooks before being aggregated. Implementing `group_by_field()` correctly requires understanding how `ClassValidationError` is constructed (which happens in `converters.py`, not in the exception class itself), what field identity means in the context of attrs slots vs. dict keys, and where the test coverage boundary is. The synthetic fixture has no equivalent dispatch complexity.

---

## Fixture Testing Methodology

**Recommended approach: vendored snapshot (git archive at pinned SHA, checked in to `tests/fixtures/`)**

Each fixture is a directory snapshot, not a live clone. This is a hybrid of the vendored-snapshot approach with a lightweight pytest layer.

### Reproducibility

Versions are pinned by SHA in `tests/fixtures/<name>/FIXTURE_META.json`:

```json
{
  "upstream": "https://github.com/keleshev/schema",
  "sha": "a90e49bba3fb25f1e8a9bf0b6c6b6fd17ed4e793",
  "captured": "2026-03-18"
}
```

The snapshot was produced with:

```bash
git archive --format=tar <SHA> | tar -xC tests/fixtures/<name>/source/
```

Git history is stripped entirely — only the working tree is captured. The agent sees a codebase with one commit (the initial import), not the upstream history. Commit messages that describe future changes are therefore invisible.

### Isolation

Each test gets a fresh `tmp_path` copy. The vendored snapshot is read-only; tests copy it to `tmp_path`, run `git init`, and make an initial commit. The original in `tests/fixtures/` is never mutated.

### pytest Integration

`repo_with_remote(fixture_name)` is extended to accept a fixture name. When a name is given, the source is copied from `tests/fixtures/<name>/source/` instead of the synthetic template:

```python
@pytest.fixture()
def repo_with_remote(request, tmp_path):
    fixture_name = getattr(request, "param", None)
    source = (
        FIXTURES_DIR / fixture_name / "source"
        if fixture_name
        else FIXTURES_DIR / "template"
    )
    ...
```

Tests opt in by parametrizing:

```python
@pytest.mark.parametrize("repo_with_remote", ["schema"], indirect=True)
def test_error_message_change(repo_with_remote):
    ...
```

### CI/CD Impact

The five snapshots total approximately 3–5 MB of Python source. This is negligible for CI clone time. No network calls are made at test time — everything is checked in. The snapshot refresh script (`scripts/refresh_fixtures.py`) is run manually when upstream pinned SHAs are updated; it is not part of CI.

---

## Implementation Sketch

### Directory Layout

```
tests/
  fixtures/
    template/              # existing synthetic fixture — unchanged
    schema/
      source/              # working tree from git archive
      FIXTURE_META.json    # upstream URL + SHA + capture date
    schedule/
      source/
      FIXTURE_META.json
    environs/
      source/
      FIXTURE_META.json
    jmespath/
      source/
      FIXTURE_META.json
    cattrs/
      source/
      FIXTURE_META.json
```

### conftest.py Changes

`tests/component/conftest.py` gains one constant and one fixture update:

```python
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

@pytest.fixture()
def repo_with_remote(request, tmp_path):
    """Create a git repo + bare remote from a named fixture or the synthetic template.

    Parameterize with the fixture name to use a real-repo snapshot:
        @pytest.mark.parametrize("repo_with_remote", ["schema"], indirect=True)

    Without parametrization, falls back to tests/fixtures/template/ (synthetic).
    """
    fixture_name = getattr(request, "param", None)
    source = (
        FIXTURES_DIR / fixture_name / "source"
        if fixture_name
        else FIXTURES_DIR / "template"
    )

    repo = tmp_path / "repo"
    shutil.copytree(source, repo)

    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", ...}

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)

    git("init", "-b", "main")
    git("config", "user.email", "t@t.com")
    git("config", "user.name", "Test")
    git("add", ".")
    git("commit", "-m", "fixture: initial state")

    bare = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "--bare", str(repo), str(bare)], check=True)
    git("remote", "add", "origin", str(bare))

    return repo
```

### Marking a Test as Using a Specific Fixture

```python
@pytest.mark.parametrize("repo_with_remote", ["jmespath"], indirect=True)
def test_planner_identifies_exception_wrapping_location(repo_with_remote):
    """Research planner should locate the normalization call site."""
    ...
```

Multiple fixtures in one test module:

```python
@pytest.mark.parametrize(
    "repo_with_remote",
    ["schema", "schedule", "jmespath"],
    indirect=True,
)
def test_coding_composite_produces_passing_tests(repo_with_remote):
    ...
```

---

## Migration Path

The existing `tests/fixtures/template/` directory is unchanged. The synthetic `tmp_git_repo` fixture in `tests/component/conftest.py` is unchanged.

Migration is additive:

1. Real-repo fixtures are added alongside `template/`. No existing test is modified.
2. New integration tests reference real fixtures by name via `indirect=True` parametrization.
3. Fast component tests (worktree, DAG, executor) continue using `tmp_git_repo` or the synthetic `template/`. They do not need a real codebase.
4. SDK-marked integration tests graduate to real fixtures as the composite agents stabilize.

The synthetic fixture stays indefinitely. It is faster (no file copy), simpler (no real package structure), and appropriate for the majority of component tests that are validating orchestration mechanics, not agent intelligence.

Real-repo fixtures are reserved for tests that are explicitly measuring agent output quality — research accuracy, code correctness, test adequacy — where the synthetic fixture's triviality would make a passing test meaningless.
