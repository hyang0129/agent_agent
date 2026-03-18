# Agent Op 2 — Repo Health Check

**Parameter:** `{{TARGET_REPO}}` — the full GitHub repo path, e.g. `dbader/schedule`

Substitute `{{TARGET_REPO}}` with the actual repo before invoking this prompt.

---

You are a research agent. Your job is to determine whether `{{TARGET_REPO}}` is
**actively maintained** and **has or had a passing test suite**. This is a cursory check
— you are not running code. You are reading signals available through the GitHub API and
the repo contents.

If the repo is unhealthy, report the verdict and stop. Do not proceed to PR research.

---

## What you are looking for

A healthy fixture repo:

- Has had commits in the last 2 years
- Has a test suite (a `tests/` or `test/` directory, or `test_*.py` / `*_test.py` files)
- Shows evidence that CI was passing at some point (green badges in README, a CI config
  that runs tests, or recent merged PRs with passing checks)
- Is not archived or disabled

An unhealthy repo shows one or more of:

- No commits in 2+ years with no indication the project was intentionally frozen
- No test files at all
- CI config present but tests never run or always failing in recent history
- Repo is archived

---

## Step 1 — Basic repo metadata

```bash
gh api repos/{{TARGET_REPO}} \
  --jq '{archived: .archived, disabled: .disabled, pushed_at: .pushed_at, open_issues: .open_issues_count, stars: .stargazers_count}'
```

- If `archived: true` or `disabled: true` → **FAIL** immediately.
- Record `pushed_at`. If it is more than 2 years ago, note it — not an automatic fail,
  but factor it into the final verdict.

---

## Step 2 — Recent commit activity

```bash
gh api "repos/{{TARGET_REPO}}/commits?per_page=5" \
  --jq '[.[] | {sha: .sha[0:8], date: .commit.committer.date, message: .commit.message | split("\n")[0]}]'
```

Note the date of the most recent commit. If the last commit is more than 2 years ago,
flag it.

---

## Step 3 — Test suite presence

```bash
gh api "repos/{{TARGET_REPO}}/git/trees/HEAD?recursive=1" \
  --jq '[.tree[].path | select(test("(^|/)tests?/|test_[^/]+\\.py$|[^/]+_test\\.py$"))] | length'
```

If the count is 0 → **FAIL**. A repo with no test files cannot be a valid fixture.

---

## Step 4 — CI configuration

```bash
gh api "repos/{{TARGET_REPO}}/contents/.github/workflows" --jq '[.[].name]' 2>/dev/null || echo "none"
```

Also check for legacy CI:

```bash
gh api "repos/{{TARGET_REPO}}/contents" \
  --jq '[.[].name | select(test("^\\.travis\\.yml$|^tox\\.ini$|^\\.circleci$|^Makefile$|^setup\\.cfg$"))]'
```

Note whether CI exists. A repo with no CI at all is a weak signal but not an automatic fail.

---

## Step 5 — Recent CI run status (if GitHub Actions present)

If Step 4 found GitHub Actions workflows:

```bash
gh api "repos/{{TARGET_REPO}}/actions/runs?per_page=10" \
  --jq '[.workflow_runs[] | {name: .name, status: .status, conclusion: .conclusion, created_at: .created_at}]'
```

Look for recent runs. If all recent runs are failing with no passing runs in the last
6 months, flag it.

---

## Verdict

Produce a structured verdict:

```json
{
  "repo": "{{TARGET_REPO}}",
  "verdict": "pass" | "fail",
  "pushed_at": "<ISO date>",
  "last_commit_days_ago": <number>,
  "test_file_count": <number>,
  "ci_present": true | false,
  "ci_passing": true | false | null,
  "rationale": "<One or two sentences summarising the key signals.>",
  "flags": ["<any soft concerns that don't block but are worth noting>"]
}
```

**verdict = "fail"** if any of:
- `archived: true` or `disabled: true`
- `test_file_count == 0`
- Last commit is more than 2 years ago **and** CI was never passing in recent history

**verdict = "pass"** otherwise.

---

## Output

Print the JSON verdict. If `verdict = "fail"`, also print:

```
REPO INELIGIBLE: <one-line reason>
```

If `verdict = "pass"`, print:

```
REPO HEALTHY: proceed to step 3 (gather PRs).
```
