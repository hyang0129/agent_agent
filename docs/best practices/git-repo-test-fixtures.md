# Git Repository Test Fixtures — Best Practices Research

**Context:** agent_agent needs realistic Python repos as test fixtures so its AI coding agent
can be exercised against real codebases. This document surveys how analogous tools handle
this problem and recommends an approach for Phase 5+.

---

## 1. The Core Problem

A fixture repo for agent_agent must satisfy several competing constraints:

- **Realistic** — enough code for the agent to have meaningful work to do (1 k–10 k lines)
- **Stable** — tests must not break because upstream changed a file
- **Isolated** — mutations made during a test must not escape the fixture
- **Fast** — no network calls during normal `pytest` runs
- **Reproducible** — same result regardless of when or where tests run

No single strategy satisfies all five without trade-offs.

---

## 2. Approaches and Trade-offs

### 2a. Clone at Test Time

Clone a public GitHub repo inside a pytest fixture, run the test, then delete the clone.

**Pros:** Always reflects a real project; no maintenance burden.
**Cons:** Slow (clone latency), network-dependent (CI outages), non-reproducible if the
upstream repo changes or disappears, requires internet access in air-gapped environments.

**Verdict:** Acceptable only for smoke tests marked `@pytest.mark.network` that are
explicitly excluded from normal CI runs.

---

### 2b. Clone + Pin to a Commit (network-at-setup-time)

Clone once, record the commit SHA, and re-clone to that SHA in CI using a cache layer.
Docker layer caching or a CI artifact can preserve the clone between runs.

```bash
git clone --depth=1 --branch v1.2.3 https://github.com/org/repo /tmp/fixture
git -C /tmp/fixture reset --hard <pinned-sha>
```

This is exactly what **SWE-bench** does. Each evaluation instance pins
`base_commit` (the commit the issue was filed against). The harness `setup_repo.sh`
clones the repo, resets to that SHA, strips remote history with
`git reflog expire --expire=now && git gc --prune=now --aggressive`,
and deletes all tags pointing to commits after the target timestamp.
This produces a deterministic, history-cleaned snapshot embedded into a Docker image.

**Pros:** Deterministic once pinned; real repo history and structure.
**Cons:** Still requires a one-time network hit; Docker image or build artifact must be
managed; image gets stale as dependencies age.

**Verdict:** The right model for heavyweight evaluation harnesses. Too much infrastructure
for unit/component tests.

---

### 2c. Vendored Snapshot (current agent_agent approach, extended)

Copy a real repo's files into `tests/fixtures/` at a known commit, commit that snapshot to
the agent_agent repo, and never network-fetch during tests. The fixture `repo_with_remote`
(or an equivalent) copies the snapshot into `tmp_path`, runs `git init`, and wires a bare
remote so the agent can push.

**Current state:** agent_agent already does this with a custom-built ~30-file template
in `tests/component/conftest.py` (`tmp_git_repo` fixture).

**Scaling up to a real repo:** Pick a small, stable OSS Python package (e.g.,
`requests`, `httpx`, or a well-maintained utility library). Export a single commit with
`git archive`:

```bash
cd /tmp
git clone --depth=1 https://github.com/encode/httpx httpx-src
git -C httpx-src archive HEAD | tar -x -C /path/to/agent_agent/tests/fixtures/httpx/
# commit the result — no .git/, just the working-tree files
```

The fixture then recreates a git repo from these files at test time, just as `tmp_git_repo`
does today.

**Pros:** Completely offline; stable; fast; no Docker required; the fixture evolves only
when a developer explicitly updates it and re-commits.
**Cons:** Checked-in fixture adds repo size; must be deliberately refreshed when you want
a newer version of the upstream; doesn't carry git history (though that is rarely needed
for agent testing).

**Verdict:** Best fit for agent_agent's component tests. See §5 for the recommended
implementation.

---

### 2d. `git bundle` — Single-File Snapshot with History

`git bundle create fixture.bundle --all` packages an entire repo (with full history) into
one portable binary file. The fixture then does `git clone fixture.bundle /tmp/work`.

**Pros:** Preserves full git history; portable single file.
**Cons:** Bundles are binary blobs — diffs are unreadable, so PR reviews are blind to
changes inside the fixture. Large bundles slow down `git clone` at test time.

**Verdict:** Useful when the agent needs to inspect git log or blame. For code-editing
tests, working-tree files suffice; avoid the complexity.

---

### 2e. Programmatically Generated Synthetic Repos

Build a fake Python package in the fixture using `pathlib` and subprocess git calls
(what `tmp_git_repo` currently does). Scale it up by writing more files, more modules,
more tests.

**Pros:** Zero external dependencies; the fixture is self-documenting; easy to introduce
specific buggy patterns the agent must fix.
**Cons:** Synthetic code lacks the idiosyncrasies of real codebases (unusual import
patterns, legacy style, mixed conventions) that make agent testing valuable at scale.

**Verdict:** Keep for small fixtures that test a specific agent behavior. For realism
tests, prefer a vendored real repo.

---

## 3. Isolation Strategies

All approaches above need mutation isolation so one test's changes don't bleed into the
next.

### `tmp_path` copy (current approach, recommended)

```python
import shutil

@pytest.fixture()
def fixture_repo(tmp_path):
    src = Path(__file__).parent.parent / "fixtures" / "httpx"
    dest = tmp_path / "httpx"
    shutil.copytree(src, dest)
    _git_init(dest)   # git init + initial commit
    return dest
```

`tmp_path` is function-scoped by default. pytest cleans it up (retaining the last 3 runs
for debugging). This is the pattern used by **pre-commit**'s test suite (`make_repo`
copies a template directory into a fresh `git_dir()`).

### `git worktree` copy

For session-scoped fixtures where copying is too expensive, create a bare clone of the
fixture bundle then use `git worktree add` per test:

```python
bare = tmp_path_factory.mktemp("bare") / "repo.git"
subprocess.run(["git", "clone", "--bare", str(bundle_path), str(bare)])

@pytest.fixture()
def worktree(tmp_path):
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(bare), "worktree", "add", str(wt)])
    yield wt
    subprocess.run(["git", "-C", str(bare), "worktree", "remove", "--force", str(wt)])
```

This is fast (worktree creation is cheap) and produces a fully independent working tree.
agent_agent's `WorktreeManager` already implements this pattern for composite agents.

### Docker / container isolation

Used by SWE-bench for full evaluation runs. Each instance gets its own container with the
repo pre-loaded. Provides the strongest isolation but requires Docker infrastructure and
adds minutes of startup overhead. Inappropriate for component tests; appropriate for
end-to-end evaluation benchmarks.

---

## 4. How SWE-bench Handles This (the Closest Analog)

SWE-bench is the gold standard for AI coding agent evaluation on real repos.

**Dataset:** Each instance records `repo`, `base_commit`, `patch` (the ground-truth fix),
and `test_patch` (the regression test). There are 2,294 instances across 12 Python repos.

**Fixture strategy:**
1. A `setup_repo.sh` script is generated per instance. It clones the repo,
   resets to `base_commit`, strips future history (`git gc --prune=now --aggressive`),
   removes the remote, and verifies no post-timestamp commits exist.
2. This script is baked into a Docker image (`swebench/sweb.env.<repo>:<base_commit>`).
3. Each evaluation run starts a fresh container from that image, applies the model's patch,
   and runs the test suite.

**Key design decisions that agent_agent should borrow:**
- Pin to a specific SHA, not a branch name.
- Strip future history from the fixture so the agent cannot "see" the solution.
- Always start from a clean known state (the container image plays the role of `shutil.copytree`).
- Run the test suite before and after applying the patch to determine pass/fail.

---

## 5. Recommended Approach for agent_agent Phase 5+

### Tier 1 — Existing synthetic fixture (keep for fast unit/component tests)

Keep `tmp_git_repo` as-is. It is fast, has zero dependencies, and exercises the agent's
plumbing without worrying about realism.

### Tier 2 — Vendored real repo snapshot (new, for realism tests)

**Step 1 — Choose a fixture repo.**
Pick a small, well-structured Python package with: an existing test suite, a
`pyproject.toml`, type hints, and no binary assets. Good candidates:
- `encode/httpx` (~12 k lines, realistic HTTP client code)
- `Textualize/rich` (~15 k lines, well-tested, readable)
- A simpler option: `python-attrs/cattrs` (~5 k lines)

The fixture repo should have known, fixable bugs or improvement opportunities that can be
expressed as GitHub issues for the agent to resolve.

**Step 2 — Export and vendor.**

```bash
# from the agent_agent repo root
FIXTURE_REPO=httpx
FIXTURE_SHA=<pinned-sha>   # record in fixtures/httpx/FIXTURE_META.json

git clone --depth=1 https://github.com/encode/httpx /tmp/httpx-src
git -C /tmp/httpx-src reset --hard $FIXTURE_SHA
mkdir -p tests/fixtures/$FIXTURE_REPO
git -C /tmp/httpx-src archive HEAD | tar -x -C tests/fixtures/$FIXTURE_REPO
echo "{\"repo\": \"encode/httpx\", \"sha\": \"$FIXTURE_SHA\"}" \
  > tests/fixtures/$FIXTURE_REPO/FIXTURE_META.json
git add tests/fixtures/$FIXTURE_REPO
git commit -m "chore: vendor httpx fixture at $FIXTURE_SHA"
```

**Step 3 — Wire a pytest fixture.**

```python
# tests/component/conftest.py  (addition)
FIXTURE_HTTPX = Path(__file__).parent.parent / "fixtures" / "httpx"

@pytest.fixture()
def httpx_repo(tmp_path: Path) -> Path:
    """Real-world Python repo (httpx) in a fresh git repo, isolated per test."""
    dest = tmp_path / "httpx"
    shutil.copytree(FIXTURE_HTTPX, dest,
                    ignore=shutil.ignore_patterns("FIXTURE_META.json"))
    _git_init(dest)   # reuse existing git init helper
    return dest
```

**Step 4 — Mark tests that use the heavy fixture.**

```python
@pytest.mark.slow
def test_coding_agent_adds_missing_type_hint(httpx_repo, ...):
    ...
```

Run with `pytest -m "not slow"` for fast CI; include `slow` in nightly or PR CI.

### Tier 3 — Bundle + worktree (if copy cost becomes a problem)

If `shutil.copytree` of a 15 k-line repo becomes measurably slow (it rarely is — file
copies are fast), replace it with a `git bundle` stored in fixtures and `git worktree add`
per test. Measure first.

### Tier 4 — Docker evaluation harness (Phase 6+, not yet)

If agent_agent adds a benchmark mode (compare against SWE-bench instances), adopt the
SWE-bench Docker image strategy. This is orthogonal to the component test infrastructure.

---

## 6. Pytest Plugins and Tooling

| Plugin | What it provides | Verdict for agent_agent |
|--------|-----------------|------------------------|
| `pytest-git` | `git_repo` fixture (empty repo via GitPython; auto-cleanup) | Useful but thin — the existing `tmp_git_repo` fixture already does this without the extra dependency |
| `pytest-tmp-files` | Declarative dict-based file hierarchy creation | Helpful for building the synthetic fixture programmatically; low priority |
| `pytest-xdist` | Parallel test execution | Valuable once the slow fixture suite grows; `tmp_path` isolation makes it safe |
| `pytest-timeout` | Per-test wall-clock timeout | Important for agent tests that can hang; add to `pyproject.toml` |
| `vcrpy` / `responses` | HTTP cassette recording/playback | Already using `respx` for mock HTTP; no change needed |

**No plugin is strictly required.** The combination of `tmp_path` + `shutil.copytree` +
subprocess git commands is sufficient and already proven by pre-commit, GitPython, and DVC.

---

## 7. Summary of Best Practices

1. **Vendor, don't clone at test time.** Network fetches in pytest are fragile. Pin a SHA,
   export with `git archive`, and commit the working tree files.

2. **Always isolate with `tmp_path` copy.** Never operate on the fixture directory itself.
   One `shutil.copytree` per test is cheap and gives perfect isolation.

3. **Pin to a SHA, record metadata.** Store `FIXTURE_META.json` with the source repo and
   commit so the fixture can be audited and refreshed deliberately.

4. **Strip future history from the fixture.** The agent should not be able to read git log
   entries that describe the fix it is supposed to implement. A fresh `git init` + one
   initial commit achieves this automatically.

5. **Tier your fixtures by cost.** Fast synthetic fixtures for unit/component tests; slow
   vendored real repos behind `@pytest.mark.slow` for realism tests.

6. **Add `pytest-timeout`.** Agent invocations can hang. A 120 s wall-clock timeout per
   test prevents CI from blocking indefinitely.

7. **Keep the fixture updatable.** Document the update procedure (the `git archive` steps
   above) so any developer can refresh the vendor snapshot when a new fixture version is
   needed.
