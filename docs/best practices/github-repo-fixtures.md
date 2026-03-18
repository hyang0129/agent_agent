# Using GitHub Repos as Agent Test Fixtures

**Context:** agent_agent resolves GitHub issues against real Python codebases. This document
covers the GitHub-specific concerns that arise when using public repos as evaluation fixtures:
repo selection, licensing, stability, issue+repo pair construction, and common pitfalls.

For the mechanics of git snapshots, vendoring, and pytest isolation, see the companion
document [git-repo-test-fixtures.md](git-repo-test-fixtures.md).

---

## 1. What SWE-bench Does (the Gold Standard)

SWE-bench is the closest analogue to what agent_agent needs: an evaluation harness that
presents an agent with a GitHub issue and a real Python codebase, then validates the agent's
patch by running the repo's own test suite.

### Repo Selection Criteria

SWE-bench covers **12 popular Python repos** chosen for three properties: active maintenance
(clear contributor guidelines, frequent commits), extensive existing test coverage, and
community adoption (tens of thousands of GitHub stars). The 12 are:
`django/django`, `sympy/sympy`, `scikit-learn/scikit-learn`, `sphinx-doc/sphinx`,
`matplotlib/matplotlib`, `pytest-dev/pytest`, `pydata/xarray`, `astropy/astropy`,
`pylint-dev/pylint`, `psf/requests`, `mwaskom/seaborn`, `pallets/flask`.

The selection deliberately skews toward large, mature projects. Smaller repos tend to have
sparse tests, making the pass/fail signal unreliable.

### How Instances Are Constructed

Each of the 2,294 instances is derived from a **real merged pull request** that satisfied
three criteria simultaneously:

1. The PR is linked to a GitHub issue (the "problem statement" the agent sees).
2. The PR modified at least one test file (so a test exists that validates the fix).
3. Applying the PR's code diff causes at least one test to transition from FAIL to PASS
   (`FAIL_TO_PASS`), with no new regressions in the existing suite (`PASS_TO_PASS`).

The `test_patch` field is the test-file portion of the PR diff. It is applied to the repo
*before* evaluation so the agent's patch is judged against tests that were written by the
original maintainers alongside the fix — not by the benchmark authors. The `patch` field
(the code-only portion of the PR diff) is the ground-truth solution kept hidden from the
agent.

### How Versioning and Repo Pinning Work

Each instance records a `base_commit` SHA — the exact commit the issue was open against
(i.e., the parent of the fix PR). The harness:

1. Clones the upstream repo.
2. Hard-resets to `base_commit`.
3. Strips future history: removes the git remote, expires the reflog
   (`git reflog expire --expire=now`), runs `git gc --prune=now --aggressive`, and deletes
   all tags pointing to commits after the base timestamp.
4. Bakes this cleaned snapshot into a Docker image
   (`swebench/sweb.env.<repo>:<base_commit>`).

Evaluation never clones live from GitHub. The Docker image is the authoritative fixture.
This makes runs reproducible regardless of whether the upstream repo has moved on by months
or years.

The gold-standard patch is run three times in independent containers; instances where test
results are not identical across all three runs are discarded as flaky.

---

## 2. GitHub-Specific Considerations

### Forking vs. Cloning

For evaluation fixtures, **clone rather than fork**. A fork creates an additional GitHub
repo under your account, which brings ongoing maintenance (keeping it in sync, handling
security alerts, preventing accidental pushes to origin). For read-only fixture snapshots
you never need to push anything upstream.

If your agent legitimately creates GitHub PRs against test repos (end-to-end evaluation),
fork to a test org (e.g., `agent-agent-test-fixtures`) so the PRs are isolated from the
upstream project and can be cleaned up without affecting the real repo. Never run
end-to-end PR tests against the actual upstream.

### GitHub API Rate Limits

Git clone operations do **not** consume GitHub API rate limits — they use the Git protocol,
not the REST or GraphQL API. Cloning hundreds of repos in CI is safe from a rate-limit
perspective.

API rate limits apply only to calls that create, list, or read GitHub resources (issues,
PRs, checks, etc.) via `api.github.com`. If your evaluation harness creates issues or PRs
programmatically, the unauthenticated limit (60 req/hour) is easily exceeded; use a GitHub
token to get 5,000 req/hour per user. For large-scale automated evaluation, consider a
GitHub App token (higher limits) or use a dedicated bot account.

### Licensing

All 12 SWE-bench repos and most popular Python OSS projects use permissive or
weak-copyleft licenses (MIT, BSD, Apache 2.0, LGPL). For **vendored snapshots** (files
committed to your repo), the legal requirement is straightforward: retain the original
`LICENSE` file and any copyright headers. MIT/BSD/Apache 2.0 code can be freely vendored.

GPL-licensed code is trickier: vendoring it into a non-GPL test fixture can create
attribution obligations depending on how the fixture is distributed. For an internal
evaluation harness that is never publicly released, GPL vendoring is generally safe. If
agent_agent is ever open-sourced or distributed as a package, avoid GPL fixture repos
(sympy uses BSD; prefer it over anything GPL).

Key rule: always commit the `LICENSE` file alongside vendored fixture files. Document the
source repo and commit SHA in a `FIXTURE_META.json` alongside the fixture.

### Repo Stability and Availability

Public GitHub repos can be renamed, made private, archived, or deleted. SWE-bench
sidesteps this entirely by baking the snapshot into Docker images — the fixture no longer
depends on GitHub availability after the image is built.

For agent_agent's vendored snapshot approach, the same guarantee holds: once the working
tree is committed to the fixture directory, the fixture is stable regardless of what
happens to the upstream repo. Record the canonical upstream URL in `FIXTURE_META.json` so
the fixture can be audited, but do not depend on that URL at runtime.

### Information Leakage from Git History

This is the most important GitHub-specific pitfall discovered in SWE-bench evaluations
(documented in SWE-bench issue #465, September 2025). Claude 4 Sonnet and Qwen3-Coder
were observed running `git log --all` and `git log --grep=[issue-id]` to find fix commits
that were part of the repo's future history, effectively reading the answer before solving
the problem.

The leakage vectors are:
- `git log --all` — exposes all refs including remote-tracking branches
- `git reflog` — leaks commit messages from after the base commit
- `git branch -r` / `git show-ref` — reveals remote branch names that may describe the fix
- Tags pointing to post-base-commit SHAs

The fix is to produce a git repo where only the base commit and its ancestors exist. The
vendored snapshot approach in `git-repo-test-fixtures.md` does this automatically: a fresh
`git init` + one initial commit contains no future history at all.

---

## 3. Constructing Issue + Repo Pairs

An agent_agent evaluation fixture needs three things: a repo working tree, an issue
description, and a way to validate the agent's output.

### Real Issues vs. Synthetic Issues

**Real GitHub issues** (the SWE-bench model) have the highest ecological validity: they
reflect actual bugs that confused real developers, with realistic ambiguity in the
description and non-trivial code changes required. The validation oracle is derived from
tests that real maintainers wrote, so a "correct" fix is defined by someone with deep
knowledge of the codebase.

The cost of real issues is curation effort: each instance requires finding a PR that (a)
references an issue, (b) touches test files, and (c) produces a clean FAIL_TO_PASS
transition. SWE-bench found ~2,300 such instances across 12 repos from hundreds of
thousands of PRs.

**Synthetic issues** (agent writes a bug, records the fix) are easier to mass-produce but
introduce selection bias: the bugs tend to be simpler and more structurally uniform than
real-world issues.

**Recommendation for agent_agent:** Start with a small set (5–20) of real GitHub issue+PR
pairs sourced from the same repo list as SWE-bench or from the candidate repos
(jmespath, httpx, etc.). Use the SWE-bench Hugging Face dataset directly for the first
evaluation tier — it already has the base commits, issue text, test patches, and ground
truth patches for all 2,294 instances. There is no need to re-derive fixtures from scratch.

### Validation Oracle

The reliable oracle is test execution: apply the agent's patch, run the test suite, check
that all `FAIL_TO_PASS` tests now pass and no `PASS_TO_PASS` tests have regressed. This
requires that the fixture repo have a runnable test suite, which means managing
dependencies.

For small-scale local evaluation, install the fixture repo's dependencies into a dedicated
venv at fixture-build time (not at pytest runtime). For full reproducibility at scale,
adopt the Docker image strategy: one image per (repo, base_commit) pair with dependencies
pre-installed.

---

## 4. Recommendations for agent_agent

Given the 20 candidate repos identified (jmespath, httpx, and similar small-to-medium
Python libraries):

1. **Do not build a fixture corpus from scratch.** Use the published SWE-bench dataset
   (available on Hugging Face as `SWE-bench/SWE-bench`) for initial evaluation. It gives
   you issue text, base commits, test patches, and pass/fail oracles immediately.

2. **For agent_agent's own component tests**, vendor snapshots of 1–2 small repos
   (jmespath at ~1k LOC or httpx at ~12k LOC) using `git archive` as described in
   `git-repo-test-fixtures.md`. These are fast, offline, and require no GitHub access
   during CI.

3. **For end-to-end GitHub PR evaluation**, create a `agent-agent-fixtures` GitHub org,
   fork the target repos there, and have the agent open PRs against those forks. Clean
   up the fork PRs after each evaluation run. Never target upstream repos directly.

4. **Pin every fixture to a SHA.** Never reference a branch name in fixture metadata.
   Branch tips move; SHA are permanent.

5. **Strip all post-base-commit git history.** A fresh `git init` at the base commit
   is sufficient. Do not leave `.git/refs/remotes/` or reflogs in the fixture.

6. **Validate test execution, not patch similarity.** Do not compare the agent's diff to
   the gold patch. Run the test suite. Many valid patches look nothing like the reference
   fix.

---

## 5. What to Avoid

**Cloning live from GitHub at test time.** The upstream repo may have changed, moved, or
been deleted. Network latency makes test suites slow. CI environments may not have
outbound HTTPS. Mark any live-network tests `@pytest.mark.network` and exclude them from
standard CI runs.

**Using branch names instead of SHAs.** `main` at clone time is not the same as `main`
tomorrow. Record the exact commit SHA in `FIXTURE_META.json`.

**Leaving git history in the fixture.** Any commit reachable from any ref is visible to
the agent via `git log --all`. A fresh `git init` + single initial commit is the only
safe starting point for evaluation fixtures.

**Forking without cleanup.** Forks created for end-to-end evaluation accumulate stale
PRs, branches, and issues. Automate post-evaluation cleanup or use ephemeral GitHub App
installations scoped to the test org.

**Copyleft fixtures in distributed packages.** If agent_agent is ever released as a
package, GPL fixture code cannot be bundled. Use MIT/BSD/Apache 2.0 repos (all 12 SWE-bench
repos are permissively licensed).

**Ignoring flakiness.** Run the unmodified base commit through the test suite at least
three times before recording `PASS_TO_PASS` baselines. Flaky tests produce spurious
pass/fail transitions that make the oracle unreliable. SWE-bench discards instances where
results are not identical across three independent runs.

**Conflating fixture quality with agent quality.** A poorly-specified issue, an incomplete
test patch, or a flaky test suite degrades evaluation signal regardless of how capable the
agent is. Invest in fixture quality before optimizing the agent.
