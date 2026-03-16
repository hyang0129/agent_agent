# Index Construction Standard

This document defines the canonical format and construction rules for all `INDEX.md` files under `docs/`. Every folder index must conform to this standard. The standard itself is maintained here; if this document and any other index disagree, this document wins.

---

## Required Sections

Every `INDEX.md` must contain these sections in this order:

1. **H1 title** — `# <Folder Name> — Index`
2. **Purpose paragraph** — one to three sentences describing what kind of documents live in this folder and how they relate to the rest of the docs tree.
3. **Documents table** — one row per file in the folder (excluding `INDEX.md` itself).
4. **Relationship to other docs** — a short bullet list mapping this folder to adjacent folders.
5. **Adding a document** — instructions for the next author.

Sections 4 and 5 may be omitted only if the folder contains a single document that is unlikely to grow (e.g. `goals/`).

---

## Documents Table Format

```markdown
## Documents

| Document | Summary |
|----------|---------|
| [filename.md](filename.md) | <summary sentence>. Keywords: kw1, kw2, kw3. |
```

### Column rules

**Document column**
- Markdown link: display text is the bare filename, href is the relative path from the index.
- For numbered policy files, the display text should include the number: `[01-dag-orchestration.md](01-dag-orchestration.md)`.
- No trailing punctuation.

**Summary column**
- One declarative sentence that answers: *"What does this document decide or teach?"*
- Must not start with "This document" or "Covers" — lead with the subject matter.
- Followed by exactly one `Keywords:` clause on the same line, separated by a period-space.
- Keywords: 3–6 comma-separated terms. Include the primary subsystem name, key nouns, and the most likely search terms a developer would type when trying to find this document.
- Total length: 25–60 words per row. If you cannot summarise in 60 words, the document may need to be split.

### Summary examples

**Non-conforming:**
```
| [07-budget-allocation.md](07-budget-allocation.md) | Covers how token budgets work. |
```
Problems: vague verb "Covers", no Keywords clause, gives no actionable information.

**Conforming:**
```
| [07-budget-allocation.md](07-budget-allocation.md) | Defines per-DAG token budget lifecycle: top-down allocation, freeze-on-5%, stage-aware continuation rules, and human-approved increase flow. Keywords: budget, tokens, freeze, allocation, escalation, P07. |
```

---

## Relationship Section Format

```markdown
## Relationship to Other Docs

- **Policies** (`docs/policies/`) — *what* the system must do. Policies are binding.
- **Architecture** (`docs/architecture/`) — *how* subsystems implement policies.
- **Best Practices** (`docs/best practices/`) — recurring implementation patterns.
- **Theory** (`docs/theory/`) — conceptual distinctions and design philosophy.
- **Goals** (`docs/goals/`) — north-star outcomes all decisions should serve.
```

List only the folders that are meaningfully related to the current folder's content. Omit folders with no cross-cutting relationship. Each bullet uses bold for the folder name, backtick path, and an em-dash followed by a one-phrase description.

---

## Adding a Document Section Format

```markdown
## Adding a Document

Create a new `.md` file in this directory and add a row to the table above. <Folder-type> documents should:

1. <Requirement specific to this folder type>
2. <Requirement specific to this folder type>
3. Reference the policies they implement (by number, e.g. P07) — if applicable.
```

The bulleted requirements must be specific to the folder type (e.g. architecture docs state the problem being solved; policy docs list violations). Copy-pasting the same generic list across all indexes defeats the purpose.

---

## Folder-Specific Index Names

| Folder | Index filename | Rationale |
|--------|---------------|-----------|
| `policies/` | `POLICY_INDEX.md` | Uppercase signals authoritative compliance reference; consumers search for "POLICY_INDEX" by name |
| All other folders | `INDEX.md` | Standard name |

Do not introduce a third index filename without updating this table and the docs `CLAUDE.md`.

---

## When the Index Is Also the Document

A folder with exactly one document that is unlikely to grow may use that document as its own index (e.g. `goals/goals.md`). In that case:

- The file must still contain a purpose paragraph at the top explaining what the folder holds.
- No separate `INDEX.md` is created.
- If a second document is ever added, create a proper `INDEX.md` and demote `goals.md` to a regular entry.

---

## Keeping Indexes Current

An index row that does not match the file it points to is worse than no index — it misdirects Claude without warning. Treat index maintenance as part of the same commit as any document change:

| Change | Index action required |
|--------|-----------------------|
| Add a file | Add a row |
| Rename a file | Update the link href and display text |
| Change a document's purpose/scope | Rewrite the summary sentence and keywords |
| Delete a file | Remove the row |
| Move a file to another folder | Remove from old index, add to new index |
