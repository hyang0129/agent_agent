# Fixture Creation Runbook

This runbook documents how to create and maintain the real-repo integration test
fixtures for agent_agent.

The output is one JSON file per source repo under `staging/` (gitignored), named after
the repo slug (e.g. `staging/schema.json` for `keleshev/schema`). Each file is a list of
upstream repos, pinned pre-merge SHAs, and issue texts. Tests clone the upstream at the
pinned SHA on demand; nothing is vendored.

**Workflow overview:** `docs/workflows/fixture-workflow.md`

---

## Prerequisites

### Tools

| Tool | Minimum version | Install |
|------|----------------|---------|
| `bash` | 4.x | system |
| `gh` | 2.40+ | `brew install gh` or `apt install gh` |
| `jq` | 1.6+ | `brew install jq` or `apt install jq` |

### Environment variables

```bash
export TARGET_REPO=<owner/repo>                       # for steps 1–2
export AGENT_AGENT_FIXTURE_BOT_TOKEN=<github-token>   # for step 1
```

---

## Step 1 — Verify repo eligibility

Run this once before invoking any agent. Exits immediately if the repo is ineligible.

```bash
TARGET_REPO=<owner/repo> \
AGENT_AGENT_FIXTURE_BOT_TOKEN=$AGENT_AGENT_FIXTURE_BOT_TOKEN \
  ./scripts/fixtures/repo_fixture_creation/01_verify_eligibility.sh
```

**Checks:**
- License permits commercial use (blocks CC-NC, SSPL, BSL, no-license)
- Primary language is Python (only permitted language for now)
- No vendored dependency tree (`vendor/`, `node_modules/`, `Godeps/`, etc.)

Exit 0 = eligible, proceed to step 2. Exit 1 = ineligible, choose a different repo.

---

## Step 2 — Repo health check (agent op)

Prompt file: `scripts/fixtures/repo_fixture_creation/prompts/02_repo_health_prompt.md`

Substitute `{{TARGET_REPO}}` with the full repo path before invoking. The agent checks:
- Recent commit activity
- Test suite presence
- CI configuration and passing status

If the verdict is `fail`, choose a different repo. If `pass`, proceed to step 3.

---

## Step 3 — Gather PRs (agent op)

Prompt file: `scripts/fixtures/repo_fixture_creation/prompts/01_pr_research_prompt.md`

Substitute `{{TARGET_REPO}}` with the full repo path before invoking. The agent writes
5 entries to `staging/<repo-slug>.json` (e.g. `staging/schema.json`).

After the agent completes:

```bash
jq 'length' staging/<repo-slug>.json   # should print 5
```

---

## How to add a new fixture

1. Run steps 1–2 with the new target repo.
2. Run step 3 (research agent) with `{{TARGET_REPO}}` substituted.
3. Review `staging/<repo-slug>.json` and promote when satisfied.

---

## How to remove a candidate

```bash
jq 'map(select(.fixture_id != "<fixture_id>"))' \
  staging/<repo-slug>.json > staging/<repo-slug>.json.tmp
mv staging/<repo-slug>.json.tmp staging/<repo-slug>.json
```

---

## Artifact locations

| Artifact | Location | Committed? |
|----------|----------|-----------|
| `<repo-slug>.json` | `staging/<repo-slug>.json` | No (gitignored, until promoted) |
