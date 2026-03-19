# CLAUDE.md Best Practices

A distillation of patterns from high-performing teams, Anthropic's own internal usage, and community experience.

---

## The Core Mental Model

CLAUDE.md is **persistent context**, not a manual. Every line loads on every session and competes for Claude's finite attention budget. The system prompt Claude Code already includes ~50 instructions; LLMs follow ~150–200 instructions with consistency. Bloat directly hurts adherence to rules that matter.

**Write it like a senior engineer's onboarding notes to a capable new hire who knows the language and framework but not your repo.**

---

## The Four Scopes

| File | Scope | Committed? | Purpose |
|------|-------|-----------|---------|
| `~/.claude/CLAUDE.md` | Global | No (personal) | How *you* work: preferred tools, personal style |
| `./CLAUDE.md` | Project root | Yes | How *this repo* works: team-shared context |
| `./subdir/CLAUDE.md` | Subdirectory | Yes | How *this service* works: auto-loaded on entry |
| `.claude/settings.local.json` | Machine-local | No (gitignore) | Personal MCP paths, terminal quirks |

**Rule of thumb:** Global says how I work. Project says how this repo works. Subdirectory says how this service works.

---

## What to Include

### 1. Non-obvious commands

The highest-value section. Document commands that are non-obvious, project-specific, or have critical flags. Claude can read your code and infer architecture, but it cannot infer your muscle memory.

```markdown
## Commands
- Tests: `pytest tests/ -x -v` (single tests only; full suite takes 8 min)
- Migration: `dotnet ef migrations add <name> --project src/Infra --startup-project src/API`
- Lint: `rg --no-heading --fixed-strings` (not grep)
- Never use interactive git diff; use `git diff --no-pager`
```

### 2. Architecture map (brief)

5–10 lines that tell Claude where things live so it doesn't waste context exploring.

```markdown
## Architecture
- app/models/     — thin models: validations and associations only, no business logic
- app/commands/   — state-changing operations, email triggers, multi-step processes
- app/controllers/ — routing and serialization only
- tests/          — mirrors app/ structure
```

### 3. Code style divergences — not defaults

Include only where your project diverges from what Claude would naturally produce. "Write clean code" is wasted tokens. "Use f-strings, not .format()" is worth including.

```markdown
## Style
- ES modules only (import/export), never require()
- TypeScript strict mode; no `any`, use `unknown`
- Functional-first; OOP only at service boundaries
- Errors must be raised explicitly; no silent fallbacks
```

### 4. Testing rules

Framework, how to run, what to avoid.

```markdown
## Testing
- Framework: pytest
- Run single tests during development, not the full suite
- Write tests before implementation (TDD)
- Avoid mocks unless testing external I/O
- Warnings must be clean; never ignore them
```

### 5. Git and PR etiquette

Prevent Claude from doing things like committing to main or skipping hooks.

```markdown
## Git
- Never commit directly to main; always branch
- Commit format: type(scope): description
- Never use --no-verify
- Never add co-authored or attribution lines to commit messages
```

### 6. Gotchas

Things Claude consistently gets wrong on *this specific project*. The most under-rated section.

```markdown
## Gotchas
- Never pass --foo-bar; use --baz instead (foo-bar causes silent data loss)
- Auth uses session tokens, not JWTs — do not mix them up
- The events table is append-only; never UPDATE or DELETE rows
```

### 7. Environment requirements

Non-obvious env setup Claude needs to know before running commands.

```markdown
## Environment
- Python: `pyenv use 3.11` before running anything
- Required: DATABASE_URL in .env; SDK auth uses claude CLI credentials (~/.claude/), not an API key
- Build: always cmake -G Ninja, never Make
```

---

## What to Exclude

| Do not include | Why |
|----------------|-----|
| Things Claude can infer from reading the code | Every inferable line dilutes real signal |
| Standard language conventions ("use PEP 8") | Claude already knows this |
| Full API docs or large reference material | Link to them instead |
| Information that changes every sprint | Becomes a liability when stale |
| "Be thorough," "write clean code" | No behavioral effect; pure noise |
| Style rules your linter already enforces | Use the linter; hooks are guarantees, CLAUDE.md is advisory |
| File-by-file codebase narration | An architecture map is good; narrating every file is not |
| Detailed tutorials or lengthy explanations | A one-line pointer to docs is better |

---

## Structural Patterns That Work

### Pattern 1: Progressive Disclosure (best for mid-size repos)

Keep root CLAUDE.md brief (~50–80 lines). Store specialized context in `agent_docs/` and reference it conditionally.

```
CLAUDE.md                        ← universal rules, ~50–80 lines
agent_docs/
  building.md
  testing.md
  conventions.md
  architecture.md
```

**Critical detail:** Use conditional references, not `@`-embeds. `@path/to/file` *unconditionally* injects the file on every session. Conditional references ("When adding CSS, read docs/ADDING_CSS.md first") only activate when relevant.

### Pattern 2: Hierarchical Subdirectory (best for monorepos)

Each service owns its context. Auto-loaded when Claude enters that directory. One team cut their effective CLAUDE.md from 47k to 9k characters (80% reduction) using this pattern.

```
CLAUDE.md                    ← repo etiquette, shared tooling
frontend/CLAUDE.md           ← React patterns, component conventions
backend/CLAUDE.md            ← API design, service patterns
infrastructure/CLAUDE.md     ← IaC, deployment specifics
```

Each file stays under ~9k characters / 200 lines.

### Pattern 3: Positive Example + Anti-Pattern (best for style rules)

Abstract rules are forgettable. Concrete examples with explicit anti-patterns are not.

```markdown
## Models
Thin models only — validations, associations, and scopes:

✅  `validates :email, presence: true, uniqueness: true`
✅  `scope :active, -> { where(active: true) }`
❌  Never: `def send_welcome_email` in a model (put that in a command)
```

### Pattern 4: The Living Document

After Claude makes a mistake, update CLAUDE.md to prevent it from recurring. Treat the file as code: review it when things go wrong, prune when they go right, commit it so the team contributes. This is the primary flywheel for improving Claude's behavior on your project.

---

## Improving Adherence to Critical Rules

If Claude keeps ignoring a rule, the file is probably too long. Fix that first. Beyond length:

| Technique | How to use |
|-----------|-----------|
| Emphasis markers | `IMPORTANT:` or `YOU MUST` for rules that must never break |
| Conditional triggers | "When X, do Y" activates the rule at the right moment |
| Positive + negative | "Never use --foo; use --bar instead" eliminates ambiguity |
| Hooks for hard rules | If it must never break, use a hook — not CLAUDE.md |
| Slash commands | For recurring workflows, `.claude/commands/` is more reliable than prose |

---

## CLAUDE.md Is One Layer

CLAUDE.md is not the only tool. Use the right tool for each job:

| Tool | Purpose | When to use |
|------|---------|------------|
| `CLAUDE.md` | Persistent universal context | Always-applicable project rules |
| Hooks (`.claude/settings.json`) | Deterministic enforcement | Rules that must never be broken |
| Skills (`.claude/skills/`) | Domain knowledge, loaded on demand | Context needed sometimes, not always |
| Slash commands (`.claude/commands/`) | Reusable prompt templates | Recurring workflows |
| Sub-agents | Specialized, context-isolated workers | Security review, research, long tasks |

Using CLAUDE.md as a catch-all for all five categories is the primary anti-pattern.

---

## Size Targets

| File | Target | Hard limit |
|------|--------|-----------|
| `~/.claude/CLAUDE.md` (global) | 15–30 lines | 60 lines |
| Project root `CLAUDE.md` | 50–100 lines | 200 lines |
| Subdirectory `CLAUDE.md` | 30–80 lines | 200 lines |
| `agent_docs/` individual files | Any | Keep focused |

**Warning sign:** If Claude keeps violating a rule that's written in your CLAUDE.md, the file is too long.

---

## Minimal Template

```markdown
# Project: [Name]

## Commands
- [test command with exact flags]
- [build command]
- [lint command]

## Architecture
- [module]: [one-line description]
- [module]: [one-line description]

## Style
- [divergence from default 1]
- [divergence from default 2]

## Testing
- Framework: [name]
- [how to run, what to avoid]

## Git
- [branch convention]
- [commit format]
- [what never to do]

## Gotchas
- [thing Claude gets wrong on this project]
- [thing Claude gets wrong on this project]
```

Under 60 lines. Additional context belongs in subdirectory files or conditional `agent_docs/` references.

---

## What the Best Teams Have in Common

- **Short root files.** The best CLAUDE.md files are short and dense with real signal.
- **Hierarchical context.** Large repos use subdirectory CLAUDE.md files. Root is universal; subdirs are specialized.
- **Gotchas over guidelines.** Documenting what Claude gets wrong is higher leverage than writing a comprehensive style guide.
- **Hooks for enforcement.** Critical rules live in hooks, not in CLAUDE.md prose.
- **Commands for workflows.** Recurring prompt patterns live in `.claude/commands/`, not in CLAUDE.md.
- **Treat it as code.** Review it on mistakes, prune it when things work, commit it to version control.
