# Agent Op 8 — Fixture Audit Prompt

You are an audit agent. Your job is to evaluate one fixture repo that was created in
Steps 3–7 and produce (or append to) a written audit report. The fixtures will be used
as integration test inputs for an autonomous coding agent — the audit ensures each
fixture is solvable, unambiguous, and free of information leakage.

This prompt is used once per fixture. Run it once for each fixture_id you need to audit.

---

## Inputs

### 1. Identify the fixture to audit

The operator will have told you which `fixture_id` to audit before invoking this prompt.
If it is unclear, ask before proceeding.

### 2. Read the candidate metadata

```
tests/fixtures/candidate-prs.json
```

Find the entry whose `fixture_id` matches the one assigned to you. Extract: `fixture_id`,
`upstream`, `base_sha`, `issue_title`, `issue_body`, and derive the fixture repo URL as:
`https://github.com/agent-agent-fixtures/<fixture_id>`.

---

## Audit procedure

### Step 1 — Clone the fixture repo

```bash
gh repo clone agent-agent-fixtures/<fixture_id> /tmp/audit/<fixture_id>
```

Do NOT clone the upstream repo. Clone only the fixture repo in `agent-agent-fixtures`.
The fixture repo has exactly one commit ("fixture: initial state") and no git history
beyond that. Confirm this:

```bash
git -C /tmp/audit/<fixture_id> log --oneline
```

Expected output: exactly one line. If there is more than one commit, flag it as FAIL.

### Step 2 — Read the issue

```bash
gh api repos/agent-agent-fixtures/<fixture_id>/issues/1 --jq '{title: .title, body: .body}'
```

Assess:
- Is the issue title clear and specific?
- Is the issue body self-contained? Could a developer unfamiliar with the codebase
  understand what needs to be done?
- Does the issue point to a specific function, class, method, or behavior?
- Rate actionability: PASS / FLAG / FAIL (see criteria below)

### Step 3 — Check for leakage

Leakage means the fixture repo contains information that reveals the solution to an agent
that should be working from only the issue text.

Check each leakage vector:

**a) CHANGELOG / release notes**
```bash
find /tmp/audit/<fixture_id> -name "CHANGELOG*" -o -name "CHANGES*" -o -name "HISTORY*" \
  -o -name "RELEASES*" -o -name "NEWS*" | head -5
```
If any of these files exist, grep them for keywords from the issue title and PR title.
A CHANGELOG entry that describes the fix before the agent implements it is leakage.

**b) README describing the feature as already done**
Read the README. If the README documents the feature/behavior that the issue asks to add,
that is leakage — the agent can read the README and learn what the final state should look
like.

**c) Code comments or docstrings**
Search for comments that describe the fix or reference the issue number:
```bash
grep -r "TODO.*fix\|FIXME\|# issue\|# PR\|# closes\|# fixes" /tmp/audit/<fixture_id> \
  --include="*.py" -l
```

**d) Test files that already test the new behavior**
The fixture is at the pre-fix state. If a test file already contains a test for the
behavior the issue asks to add (and that test would pass), this is leakage.

Conversely, if a test exists but it currently FAILS (i.e., it tests the unfixed behavior),
that is actually correct — it is a regression test that the agent should make pass.

**e) Git history leakage**
```bash
git -C /tmp/audit/<fixture_id> log --all --oneline
git -C /tmp/audit/<fixture_id> show-ref
```
There should be exactly one commit on exactly one ref (refs/heads/main). Any additional
refs, remote-tracking branches, or tags are leakage.

### Step 4 — Assess solvability

A fixture is solvable if:
1. The issue body references or implies a specific location in the codebase
   (a function name, a class, a module, a behavior)
2. The codebase at that location actually has the unfixed code
3. The issue does not require knowledge that is not present in the repo (e.g., an external
   API spec that is not referenced anywhere in the code or tests)

Rate solvability: PASS / FLAG / FAIL

---

## Rating criteria

| Rating | Meaning |
|--------|---------|
| PASS | No issues found. Fixture is clean and ready. |
| FLAG | Minor issue that should be noted but does not block use. Operator should review. |
| FAIL | Fixture must be rebuilt before use. Blocks evaluation. |

### FAIL conditions
- More than one git commit in the fixture repo
- Git history contains any commits beyond "fixture: initial state"
- CHANGELOG contains an entry that describes the fix
- README describes the feature as already implemented
- Test file passes that tests the new behavior (fix already present in source)
- Issue is completely ambiguous (no specific function/behavior identified)

### FLAG conditions
- README contains a partial hint (mentions the function name but does not describe the fix)
- Issue body references an upstream PR number or commit SHA
- Issue body is very long and the key requirement is buried
- CHANGELOG exists but does not mention the specific fix

---

## Output

Write (or append) the audit findings to:

```
tests/fixtures/audit-report.md
```

If the file does not exist, create it with the header shown below. If it exists, append
the findings section for this fixture only — do not modify existing sections.

### Report format

#### File header (if creating from scratch)

```markdown
# Fixture Audit Report

Generated: <date>

## Summary

| fixture_id | Actionability | Leakage | Solvability | Overall |
|------------|--------------|---------|-------------|---------|
```

#### Summary table row (append one row per fixture audited)

```
| <fixture_id> | PASS/FLAG/FAIL | PASS/FLAG/FAIL | PASS/FLAG/FAIL | PASS/FLAG/FAIL |
```

The Overall rating is the worst of the three individual ratings (FAIL > FLAG > PASS).

#### Findings section (append one section per fixture audited)

```markdown
### <fixture_id>

**Overall: PASS**

- Actionability: PASS — Issue clearly identifies `SchemaError` message format and the
  expected new format.
- Leakage: PASS — No CHANGELOG. README does not describe error message format.
  One commit on main. No future-fix comments.
- Solvability: PASS — `SchemaError` is defined in `schema.py`. The unfixed message
  format is present at the base SHA.

---
```

If the overall is FLAG, include an "Operator action" line describing what the human
operator should do to resolve it.

If the overall is FAIL, include a "Rebuild required" line describing specifically what
needs to be fixed before the fixture can be used.

---

## Example (for reference only — do not copy these ratings verbatim)

```markdown
### schema-error-message

**Overall: PASS**

- Actionability: PASS — Issue clearly identifies `SchemaError` message format and the
  expected new format.
- Leakage: PASS — No CHANGELOG. README does not describe error message format.
  One commit on main. No future-fix comments.
- Solvability: PASS — `SchemaError` is defined in `schema.py`. The unfixed message
  format is present at the base SHA.

---

### schedule-deadline-bug

**Overall: FLAG**

- Actionability: PASS — Issue clearly describes the deadline-bypass bug and provides
  a reproduction recipe.
- Leakage: FLAG — `CHANGELOG.rst` exists and mentions "fix deadline handling" in an
  older entry. The entry predates the specific bug described in the issue, but the
  proximity may hint at the fix location.
- Solvability: PASS — `Job._should_run` exists in `schedule.py`. Deadline check is
  absent in the pre-fix code.

**Operator action:** Review CHANGELOG.rst lines 14–22 to confirm the older entry
does not describe the exact fix. If confirmed unrelated, promote to PASS.

---
```
