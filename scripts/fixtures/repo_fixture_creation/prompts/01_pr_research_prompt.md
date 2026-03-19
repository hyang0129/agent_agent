# Agent Op 1 — PR Research Prompt

**Parameter:** `{{TARGET_REPO}}` — the full GitHub repo path, e.g. `dbader/schedule`

Substitute `{{TARGET_REPO}}` with the actual repo before invoking this prompt.

---

You are a research agent. Search `{{TARGET_REPO}}` on GitHub and identify **10 merged pull
requests** that each fixed exactly one issue. These become integration test fixture assets
for the agent_agent evaluation harness.

Produce 10 entries in `staging/<repo-slug>.json`, where `<repo-slug>` is the last
component of `{{TARGET_REPO}}` (e.g. `staging/schema.json` for `keleshev/schema`).
Create the file if it does not exist. Do not overwrite or remove any existing entries
— only append.

---

## Complexity Distribution

You must produce exactly: **3 easy, 3 medium, 4 hard**

| Tier | Source files changed | LOC delta (non-test) | Issue clarity |
|------|---------------------|----------------------|---------------|
| easy | 1–2 | < 30 | Single unambiguous fix |
| medium | 3–5 | 30–100 | Requires reading 2–4 source files |
| hard | 4+ | 100+ | Requires architectural exploration |

---

## Step 1 — List merged PRs that close an issue

```bash
gh pr list --repo {{TARGET_REPO}} --state merged --search "Closes #" --json number,title,body,mergedAt --limit 200
gh pr list --repo {{TARGET_REPO}} --state merged --search "Fixes #" --json number,title,body,mergedAt --limit 200
gh pr list --repo {{TARGET_REPO}} --state merged --search "Resolves #" --json number,title,body,mergedAt --limit 200
```

Deduplicate by PR number. Build a candidate list.

---

## Step 2 — Inspect each strong candidate

For each candidate, get the file list and diff size:

```bash
gh pr view <number> --repo {{TARGET_REPO}} --json number,title,body,files,additions,deletions,baseRefOid
```

Classify by complexity tier using the table above. Aim to identify at least 6–8 candidates
before making final selections (to have backups).

---

## Step 3 — Parallel PR processing into difficulty pools

Maintain three pools and their targets:

| Pool | Target | Can exceed? |
|------|--------|-------------|
| easy | 3 | Yes — stop adding once you have 3+, but don't discard extras |
| medium | 3 | Yes |
| hard | 4 | Yes |

Processing stops when all three pools are at or above their targets.

Each PR from Step 2 is assigned to exactly one subagent. A PR that is dispatched is never
dispatched again regardless of outcome.

**Dispatch model:** spawn a team of subagents — one per PR — in parallel across the
current batch of unprocessed candidates. Do not process PRs sequentially; send the batch
to the team all at once. Each subagent is independent: it has no shared state with others,
receives only its own PR, and returns a structured result.

**Concurrency cap:** never have more than 10 subagents in flight at once. If a batch
would exceed 10, split it: dispatch the first 10, wait for all to complete, then dispatch
the next batch of up to 10, and so on until the batch is exhausted or all pools are full.

**Pool gate at dispatch time:** before spawning a subagent for a PR, check the current
pool counts. If the pool for that PR's likely complexity is already at or above target,
do not spawn a subagent for that PR — mark it skipped without dispatching. This is a
cheap filter based on Step 2 complexity estimates; the subagent's Check 1 is the
authoritative gate once it runs.

**In-flight subagents:** if a subagent is already running for a pool that reaches target
while it is in flight, let it finish and accept the result (do not cancel it).

Each subagent receives: PR number, PR body, repo `{{TARGET_REPO}}`, and the current pool
counts at dispatch time. It runs the three checks below in order and returns either a
result or a skip reason.

---

### Check 1 — Complexity gate

Get the file list and diff size if not already fetched in Step 2:

```bash
gh pr view <number> --repo {{TARGET_REPO}} --json files,additions,deletions
```

Classify the PR as `easy`, `medium`, or `hard` using the complexity table at the top of
this prompt (source files changed + non-test LOC delta).

**If the pool for this complexity already has 3+ (easy/medium) or 4+ (hard) accepted
entries at dispatch time:** return `skip: "pool full for <tier>"`. Do not proceed to
Check 2.

---

### Check 2 — Issue resolution

#### 2a — Collect all signals

```bash
# PR body + title
gh api repos/{{TARGET_REPO}}/pulls/<pr_number> --jq '{title: .title, body: .body}'

# All PR comments
gh api repos/{{TARGET_REPO}}/issues/<pr_number>/comments \
  --jq '[.[] | {author: .user.login, body: .body}]'

# Inline review comments
gh api repos/{{TARGET_REPO}}/pulls/<pr_number>/comments \
  --jq '[.[] | {author: .user.login, body: .body}]'
```

Extract every issue reference across body + all comments:
- Hard links: `Closes #N`, `Fixes #N`, `Resolves #N` in the PR body
- Soft links: bare `#N` anywhere in body or comments (context only — not qualifying alone)

#### 2b — Apply resolution rules

**Case 1 — Exactly one hard-linked issue:**
Use that issue. Proceed to Check 3.

**Case 2 — Two or more hard-linked issues:**
Fetch each issue's full text:

```bash
gh api repos/{{TARGET_REPO}}/issues/<number> \
  --jq '{number: .number, title: .title, body: .body, labels: [.labels[].name]}'
```

If the issues are clearly unrelated (different subsystems, one bug + one unrelated
feature), return `skip: "unrelated multi-issue"` immediately.

Otherwise, draft a synthetic issue and run the merge review subagent:

**Draft rules:**
- One title naming the mechanism, not the symptoms
- Body describes what a caller observes, under what conditions, and what they expect —
  no fix hints, no mention that it combines issues
- Must read as a single naturally-scoped issue

**Merge review subagent** receives: draft title + body, original issue texts, PR diff
summary. It returns:

```json
{
  "verdict": "single" | "split",
  "rationale": "One sentence.",
  "recommended_title": "<final title or null>",
  "recommended_body": "<final body or null>"
}
```

Verdict rules:
- `"single"`: one root cause or mechanism; a developer fixing one would naturally fix
  the other; the combined issue is more complete than either alone.
- `"split"`: issues require changes in different areas, could be independently fixed,
  or a developer would reasonably address only one.

**Pattern → `"single"`:**
> #42 "Jobs run after `until()` deadline" + #51 "`until()` not checked before first run".
> Same deadline-enforcement gap in `Job.should_run()`. One fix resolves both. → `"single"`

**Anti-pattern → `"split"`:**
> #30 "Add `run_all()` method" + #67 "Scheduler does not log job names". Independent
> features; fixing one doesn't touch the other. → `"split"`

If `"split"`: return `skip: "merge review rejected"`.
If `"single"`: use `recommended_title` and `recommended_body`. Set `synthetic_issue: true`,
`merged_from: [N, M]`. Proceed to Check 3.

**Case 3 — No hard-linked issue:**
`Related to #X` / `See #X` alone does not qualify. Return `skip: "no hard-linked issue"`.

---

### Check 3 — Confirm issues are closed

For every issue identified in Check 2:

```bash
gh api repos/{{TARGET_REPO}}/issues/<number> --jq '.state'
# Must return "closed"
```

If any issue is still open: return `skip: "open issue"`.

---

### Subagent return value

On success:

```json
{
  "status": "accepted",
  "pr_number": 123,
  "complexity": "easy",
  "issue_number": 100,
  "issue_title": "<title>",
  "issue_body": "<body>",
  "base_sha": "<from Step 4 — fetch here if not yet fetched>",
  "synthetic_issue": false,
  "merged_from": []
}
```

On skip:

```json
{
  "status": "skip",
  "pr_number": 123,
  "reason": "<skip reason>"
}
```

Add accepted results to the appropriate pool. Log skips. Continue dispatching from the
remaining candidate list until all pools are at target or candidates are exhausted.

---

## Step 4 — Get the base SHA (pre-merge state)

```bash
gh api repos/{{TARGET_REPO}}/pulls/<pr_number> --jq '.base.sha'
```

This is the 40-char SHA of the commit the repo was at **before the fix was applied**.
The fixture will snapshot the repo at exactly this commit.

---

## Step 5 — Save the resolved issue text

By this step, Step 3 has already determined which issue text to use. Save it now.

**Case 1 (single real issue):** fetch verbatim and save:

```bash
gh api repos/{{TARGET_REPO}}/issues/<issue_number> --jq '{title: .title, body: .body}'
```

Do not rephrase, trim, or summarize. The saved `issue_title` and `issue_body` are the
exact strings returned by this call.

**Case 2 (merged synthetic issue):** the `recommended_title` and `recommended_body` from
the Step 3b-ii review subagent verdict are the saved values. Do not re-fetch or re-draft.
These are final once the verdict is `"single"`.

In both cases, record what was saved in a working note before writing the output:

```
issue_title: <saved title>
issue_body:  <saved body>
source:      "verbatim" | "synthetic"
merged_from: [] | [N, M]
```

This working note is what flows directly into the output JSON — no further modification.

---

## Output

Append 10 entries to `staging/<repo-slug>.json` (e.g. `staging/schema.json`), creating
it as a JSON array if it does not exist.

```json
[
  {
    "fixture_id": "schedule-<issue-slug>",
    "complexity": "easy",
    "upstream": "https://github.com/{{TARGET_REPO}}",
    "base_sha": "<40-char SHA>",
    "license": "MIT",
    "pr_number": 123,
    "issue_number": 100,
    "issue_title": "<verbatim title, or synthetic title if merged>",
    "issue_body": "<verbatim body, or synthetic body if merged>",
    "synthetic_issue": false,
    "merged_from": []
  }
]
```

| Field | Rules |
|-------|-------|
| `fixture_id` | `<repo-shortname>-<2-4-word-slug>`, kebab-case, < 35 chars, unique |
| `complexity` | `easy`, `medium`, or `hard` — must match the tier you classified this PR into |
| `base_sha` | 40-char hex — the commit BEFORE the PR was merged |
| `issue_body` | Verbatim from GitHub, or a synthetic body combining two related issues |
| `synthetic_issue` | `false` for real single-issue fixtures; `true` if Step 3b merged two issues |
| `merged_from` | Empty array normally; `[N, M]` if two issues were merged |

Final distribution check before writing: confirm you have 3 easy, 3 medium, 4 hard.
All 10 entries must have different `pr_number` and different `issue_number`.

---

## What NOT to do

- Do not invent or rephrase issue text
- Do not select PRs that only change docs, CI config, or packaging
- Do not select PRs where the linked issue is still open
- Do not select draft PRs
- Do not use the same PR or issue number twice
- Do not remove or modify existing entries in `candidate-prs.json`

---

After writing the file, print a summary table:

| fixture_id | complexity | pr_number | issue_number | files_changed | loc_delta | rationale |
