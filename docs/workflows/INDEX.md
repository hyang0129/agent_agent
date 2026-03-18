# Workflows — Index

Step-by-step operator procedures for recurring development and maintenance tasks.
Each document describes who runs the workflow, when, what tools are involved, and what artifacts it produces.

## Documents

| Document | Summary |
|----------|---------|
| [fixture-workflow.md](fixture-workflow.md) | Defines the 3-step operator workflow (1 script + 2 agent ops): verify eligibility (license/language/vendor), health check (CI/tests/activity), gather PRs into staging/candidate-prs.json. Tests clone on demand; nothing is vendored. Keywords: fixture workflow, eligibility, health check, candidate-prs, staging, base_sha. |
