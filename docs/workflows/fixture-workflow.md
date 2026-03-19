# Fixture Creation Workflow

## Overview

This document maps the workflow for creating the real-repo integration test fixtures
for agent_agent. The sole output is `staging/candidate-prs.json` (gitignored) — a list
of upstream repos, pinned pre-merge SHAs, and issue texts. Tests clone the upstream at
the pinned SHA on demand; nothing is vendored.

The scripts are the authoritative reference for exact commands; this document is the map.

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| `TARGET_REPO` env var | `<owner/repo>`, e.g. `dbader/schedule` — used by steps 1–2 |
| `GITHUB_TOKEN` env var | GitHub token for API calls |
| `gh` CLI 2.40+ | Used by step 1 |
| `jq` 1.6+ | Used by step 1 |

Full prerequisites and setup: `scripts/fixtures/repo_fixture_creation/README.md`.

---

## Workflow Steps

| Step | Type | Script / Prompt | Scope | Input | Output |
|------|------|----------------|-------|-------|--------|
| 1 | SCRIPT | `01_verify_eligibility.sh` | Once per repo | `TARGET_REPO`, GitHub API | Exit 0 (eligible) or exit 1 (ineligible with reason) |
| 2 | AGENT | `prompts/02_repo_health_prompt.md` | Once per repo | GitHub API, `{{TARGET_REPO}}` param | JSON verdict (pass/fail) |
| 3 | AGENT | `prompts/01_pr_research_prompt.md` | Once per repo | GitHub API, `{{TARGET_REPO}}` param | 5 entries appended to `staging/candidate-prs.json` |

---

## Step 1 — Verify repo eligibility

`01_verify_eligibility.sh` checks three things before any agent is invoked:

1. **License** — blocks CC-NC, SSPL, BSL, and repos with no license. Allows anything
   that permits commercial use (MIT, BSD-*, Apache-2.0, ISC, GPL-*, LGPL-*, AGPL-*, etc.)
2. **Language** — only Python repos are permitted (hardcoded; extend when needed).
3. **Vendored dependencies** — blocks repos with a `vendor/`, `node_modules/`,
   `Godeps/`, or similar directory.

If the repo fails any check, stop and choose a different target repo.

---

## Step 2 — Repo health check (agent op)

**Prompt:** `scripts/fixtures/repo_fixture_creation/prompts/02_repo_health_prompt.md`

Substitute `{{TARGET_REPO}}` before invoking. The agent checks:

- Recent commit activity (last commit date)
- Test suite presence (`tests/` directory or `test_*.py` files)
- CI configuration and recent run status

Returns a `pass` or `fail` verdict with rationale. If `fail`, stop and choose a
different repo.

---

## Step 3 — PR Research (agent op)

**Prompt:** `scripts/fixtures/repo_fixture_creation/prompts/01_pr_research_prompt.md`

Substitute `{{TARGET_REPO}}` with the full repo path before invoking.
The agent produces all 5 entries in a single pass:

1. Lists merged PRs that close an issue via `gh pr list`.
2. Classifies candidates by complexity (easy / medium / hard).
3. Spawns a subagent team (up to 10 in flight at once) to process candidates in
   parallel into three pools (easy target 3, medium target 3, hard target 4).
4. Each subagent verifies issue resolution and confirms the issue is closed.
5. Appends 10 entries to `staging/candidate-prs.json`.

### PR complexity criteria

| Complexity | Files changed | LOC delta | Issue clarity |
|-----------|--------------|-----------|---------------|
| easy | 1–2 | < 30 | Single unambiguous requirement |
| medium | 3–5 | 30–100 | Requires reading 2–4 source files |
| hard | 4+ | 100+ | Requires architectural exploration |

Distribution: 3 easy, 3 medium, 4 hard.

---

## Artifact Manifest

| Artifact | Location | Committed |
|----------|----------|-----------|
| Candidate list | `staging/candidate-prs.json` | No — gitignored, review before promoting |
