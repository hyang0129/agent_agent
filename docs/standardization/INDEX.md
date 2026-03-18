# Standardization — Index

This folder defines cross-cutting conventions that apply to the entire `docs/` tree. Documents here are prescriptive: they set the rules that all other documents and indexes must follow.

## Documents

| Document | Summary |
|----------|---------|
| [index-construction.md](index-construction.md) | Defines required sections, table format, summary and keyword rules, and maintenance obligations for every INDEX.md in the docs tree. Keywords: index, INDEX.md, format, keywords, summary, standardization. |
| [github-repo-integration-testing.md](github-repo-integration-testing.md) | Defines the standard for Tier 3 integration tests: ephemeral GitHub repo lifecycle, fixture catalog format, fixture_repo pytest fixture usage, the 3-step fixture creation workflow, naming conventions, and bot account setup. Keywords: integration tests, fixture_repo, FixtureMeta, ephemeral repo, AGENT_AGENT_FIXTURE_BOT_TOKEN, fixture catalog, aaf- prefix, session_id. |

## Relationship to Other Docs

- **Policies** (`docs/policies/`) — index must conform to rules here; uses the special `POLICY_INDEX.md` filename.
- **Architecture** (`docs/architecture/`) — index must conform to rules here.
- **Best Practices** (`docs/best practices/`) — index must conform to rules here.
- **Theory** (`docs/theory/`) — index must conform to rules here.
- **Goals** (`docs/goals/`) — single-document folder; `goals.md` serves as its own index per the exemption defined here.

## Adding a Document

Create a new `.md` file in this directory and add a row to the table above. Standardization documents should:

1. Define a rule set that applies to more than one folder or document type.
2. Include concrete conforming and non-conforming examples wherever a rule could be interpreted ambiguously.
3. State which other documents or indexes they govern.
