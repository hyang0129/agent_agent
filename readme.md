# Agent Agent

An orchestrator that automatically creates and manages Claude agents to resolve GitHub issues through PR merging.

## Vision

A project-level orchestrator that uses a DAG to manage simultaneous issue resolution across repositories. Tasks are defined as GitHub issues; resolution is executed through automated PR creation, review, and merge.

## MVP Scope

The MVP is a **task-level orchestrator**, manually initiated by a single developer on a local system.

### MVP Flow

1. Human points the orchestrator at a GitHub issue
2. Orchestrator reads the issue and related codebase context
3. Orchestrator decomposes the task into a sub-task DAG
4. Sub-tasks are assigned to specialized agents (research, implement, test, review)
5. Agents execute in dependency order, with parallel execution where the DAG allows
6. Results are assembled into a PR with review comments
7. Human approves/merges

### What MVP Includes

- Local FastAPI server for orchestration
- GitHub issue ingestion and PR creation via `gh` CLI / GitHub API
- Sub-task DAG construction and execution
- Agent assignment based on task type (research, code, test, review)
- Context passing between dependent agents
- Basic observability (structured logging, task status)
- Dev/prod configuration separation via environment profiles

### What MVP Excludes

- Remote hosting / multi-user support
- Web UI for configuration (edit CLAUDE.md and configs locally)
- Isolated dev containers per agent (uses host dev container)
- Automatic issue discovery (human initiates each task)
- Multi-repo orchestration in a single DAG

## Architecture

TBD

## Design Policies

See docs 

## Tech Stack

- **Python 3.11** — orchestrator runtime
- **FastAPI** — local API server
- **Claude API** (Anthropic SDK) — agent execution
- **SQLite** — state persistence and checkpointing
- **GitHub CLI (`gh`)** — issue/PR interaction
- **Pydantic** — config and data validation
- **NetworkX** — DAG construction and traversal

## Integration Testing

Agent Agent is validated by pointing it at real GitHub issues on purpose-built test repositories. Each test repo is a realistic codebase with pre-written issues of known difficulty and known-correct solutions. The orchestrator's output (PRs) is evaluated against these known solutions.

See docs/architecture

## Future Scope

- Remote hosting with auth and multi-user support
- Isolated dev containers per agent (sandboxed execution)
- Project-level orchestrator managing multiple issues concurrently
- Web dashboard for monitoring and configuration
- Webhook-driven automatic issue intake
- Multi-repo support with cross-repo dependency awareness
- Team-based access controls and approval workflows
