# Docs — Working Guide

This file governs how Claude creates, places, and indexes documents in this directory.

---

## Folder Taxonomy

Route every new document to exactly one folder based on its primary purpose:

| Folder | Put here when… | Examples |
|--------|---------------|----------|
| `policies/` | The document defines *what* the system must do and *why*. Binding rules. | DAG invariants, permission rules, budget policy |
| `architecture/` | The document describes *how* a concrete subsystem is designed. Implements one or more policies. | Integration-test infra, retry/backoff design, DB schema |
| `best practices/` | The document captures a recurring *implementation pattern* that applies across multiple subsystems. | Pydantic config strategy, context window management, CLAUDE.md authoring |
| `theory/` | The document explores a *conceptual distinction* or design philosophy, not a concrete decision. | Design goals vs. design guidance, tradeoff analyses |
| `goals/` | The document describes the north-star *outcome* the system is trying to achieve. | What "done" looks like, success criteria |
| `standardization/` | The document defines cross-cutting formatting or structural conventions that govern other docs. | Index format rules, naming conventions, summary standards |
| `workflows/` | The document describes a step-by-step operator procedure for a recurring task. | Fixture creation, evaluation runs, release steps |

**If a document spans two folders:** place it where its primary audience will look; add a cross-reference line in the other folder's index.

**When in doubt:** policies > architecture > best practices > theory > goals (most concrete wins).

---

## Index Protocol

Every folder **must** have an `INDEX.md` (exception: `goals/` may use a single `goals.md` as its own index if there is only one file).

### When adding a document

1. Create the file in the correct folder.
2. Open (or create) that folder's `INDEX.md`.
3. Add one row to the documents table using this format:

```
| [filename.md](filename.md) | One-sentence purpose. Keywords: keyword1, keyword2, keyword3. |
```

The summary column must contain:
- A declarative sentence stating what the document *decides or explains* (not what it "covers").
- A `Keywords:` suffix with 3–6 terms a reader might search for.

### When updating a document

If the document's purpose or scope changes, update its index row to match. Never let index summaries lag behind file content.

### When removing a document

Remove the index row at the same time. If no files remain in a folder, remove the `INDEX.md` too.

---

## Summary Quality Rules

Good index summaries let Claude determine relevance *without opening the file*. Write summaries so that:

- The sentence answers: "What decision does this document encode?" or "What pattern does it teach?"
- The `Keywords:` list includes: the subsystem name, the key nouns, and the most likely search terms a developer would type.

**Bad:** `Covers the escalation policy for the orchestrator.`
**Good:** `Defines escalation triggers, severity tiers, structured message format, and recovery options for orchestrator failures. Keywords: escalation, retry, CRITICAL/HIGH/MEDIUM, recovery, policy gap.`

---

## Naming Conventions

- **Policies:** `NN-short-description.md` (two-digit prefix matching the policy number, e.g. `07-budget-allocation.md`).
- **Architecture / best practices / theory:** `hyphen-separated-topic.md`, no numeric prefix.
- **Goals:** `goals.md` (there is one goals document).
- No spaces in filenames.

---

## Searchability Without Reading Every File

Claude should be able to answer "which document covers X?" using only index files. To support this:

1. **Always read the folder's `INDEX.md` first**, not individual files. The index summary should be sufficient to confirm relevance.
2. **Only open a file if the index confirms it is relevant** or if the index is missing.
3. **`policies/POLICY_INDEX.md` is the single authoritative source** for all active policies. It contains per-policy summaries dense enough to check compliance without reading the full policy file. Prefer it over individual policy files unless drafting or amending a policy.
4. When creating a new document that *implements* a policy, record the policy reference (e.g. `Implements: P07`) in the document's front-matter comment or first section.

---

## Folder Indexes

Read the relevant index before opening any document. Each index contains summaries and keywords sufficient to confirm relevance without opening individual files.

| Folder | Index | Notes |
|--------|-------|-------|
| `policies/` | [policies/POLICY_INDEX.md](policies/POLICY_INDEX.md) | Canonical compliance reference — prefer this over individual policy files |
| `architecture/` | [architecture/INDEX.md](architecture/INDEX.md) | Subsystem designs and their policy references |
| `best practices/` | [best practices/INDEX.md](best%20practices/INDEX.md) | Recurring implementation patterns |
| `theory/` | [theory/INDEX.md](theory/INDEX.md) | Conceptual distinctions and design philosophy |
| `goals/` | [goals/goals.md](goals/goals.md) | Single file serves as its own index |
| `standardization/` | [standardization/INDEX.md](standardization/INDEX.md) | Cross-cutting formatting and structural conventions |
| `workflows/` | [workflows/INDEX.md](workflows/INDEX.md) | Operator procedures for recurring development tasks |

> **Index format rules** are defined in [standardization/index-construction.md](standardization/index-construction.md). If this file and that document disagree on formatting, the standardization document wins.

---

## Adding a New Folder

Only add a new top-level folder if none of the existing categories fits *and* you expect at least three documents to live there. When adding:

1. Create the folder.
2. Create `INDEX.md` with a description and an empty documents table.
3. Add a row for the new folder to the taxonomy table above.
4. Add a row for the new folder to the Folder Indexes table above.
