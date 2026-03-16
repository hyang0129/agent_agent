# Best Practices — Index

Recurring implementation patterns that apply across multiple subsystems.

## Documents

| Document | Summary |
|----------|---------|
| [dev-prod-separation.md](dev-prod-separation.md) | Establishes the environment configuration strategy using pydantic-settings, per-environment `.env` files, safety rails for dev, and separate databases per environment. Keywords: pydantic-settings, environment, dev, prod, config, safety rails, .env. |
| [claude-md-best-practices.md](claude-md-best-practices.md) | Defines what to include and exclude in CLAUDE.md, the four scope levels, structural patterns, size targets, and the flywheel for improving adherence. Keywords: CLAUDE.md, context, persistent context, size, structure, adherence, gotchas. |
| [claude-md-context-strategy.md](claude-md-context-strategy.md) | Defines the three-layer model for distributing agent guidance across CLAUDE.md (universal), system prompt injection (per-agent-type), and DAG context injection (task-specific) to preserve adherence budget. Keywords: CLAUDE.md, system prompt, context injection, ContextProvider, agent guidance, adherence, layers. |

## Relationship to Other Docs

- **Policies** (`docs/policies/`) — binding rules that best practices implement in concrete patterns.
- **Architecture** (`docs/architecture/`) — subsystem designs that apply these patterns to specific components.
- **Theory** (`docs/theory/`) — conceptual distinctions that motivate some of the patterns here.

## Adding a Document

Create a new `.md` file in this directory and add a row to the table above. Best-practices documents should:

1. Describe a pattern that recurs across at least two subsystems (not a one-off solution).
2. Include concrete conforming and non-conforming examples wherever a rule could be misapplied.
3. Reference the policies they implement (by number, e.g. P07) — if applicable.
