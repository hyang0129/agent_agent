# Audit Ephemeral GitHub Repos

List all GitHub repos for the authenticated user, use the claude CLI to
cross-check for anything that looks non-ephemeral, then present a full audit
report. Read-only — nothing is deleted.

## Steps

### 1. Fetch the full repo list

Run:
```
gh repo list --limit 500 --json name,description,createdAt,isPrivate,pushedAt
```

Capture the JSON output. Note the total count.

### 2. Identify ephemeral repos by naming pattern

Known ephemeral patterns from agent_agent:
- `aaf-<8-char-hex>-<slug>` — test fixture repos created by the SDK test suite
- `agent-agent-test-<8-char-hex>` — orchestrator integration test repos

Everything else is presumed non-ephemeral unless its description contains
the word "Ephemeral".

Partition the full list into:
- **ephemeral** — matches a pattern above OR description contains "Ephemeral"
- **non-ephemeral** — everything else

### 3. Ask the claude CLI to cross-check for false negatives

Build a prompt that contains:
- The list of repos you classified as **non-ephemeral** (name + description + createdAt)
- The instruction: "Review this list of GitHub repositories. Flag any that look
  like they might actually be ephemeral test or fixture repos that were missed
  by the naming pattern check. Look for: generic/nonsense names, names with
  hex suffixes, descriptions mentioning 'test', 'fixture', 'tmp', 'temp',
  'demo', 'scratch', or similar. For each flagged repo give a one-line reason.
  Be conservative — if in doubt, say keep."

Run:
```
claude -p "<prompt>"
```

Capture the response.

### 4. Present the audit report

Print:

```
╔══════════════════════════════════════════════════════════════╗
║           AGENT_AGENT EPHEMERAL REPO AUDIT REPORT           ║
╚══════════════════════════════════════════════════════════════╝

Total repos found: N

──────────────────────────────────────────────────────────────
 EPHEMERAL  (matched naming pattern or description)
──────────────────────────────────────────────────────────────
  List each repo: name | description | created | visibility
  Group by pattern:
    aaf-* repos: N
    agent-agent-test-* repos: N
    description-matched: N

──────────────────────────────────────────────────────────────
 CLAUDE'S CROSS-CHECK  (possible false negatives in keep list)
──────────────────────────────────────────────────────────────
  Paste Claude's response verbatim here.

──────────────────────────────────────────────────────────────
 NON-EPHEMERAL — KEEP
──────────────────────────────────────────────────────────────
  List each repo: name | description | created | visibility

──────────────────────────────────────────────────────────────
 SUMMARY
──────────────────────────────────────────────────────────────
  Ephemeral (safe candidates) : N
  Flagged by Claude           : N
  Non-ephemeral (keep)        : N
```

### 5. Print deletion commands but do not run them

After the report, print the commands the user would need to run to delete
the ephemeral repos, but do not execute them:

```
# To delete ephemeral repos, review the list above then run:
gh repo delete hyang0129/<name> --yes
# (repeat for each repo)
```

Make clear that the user must run these manually after reviewing.
