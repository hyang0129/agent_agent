# Workflows — Index

Step-by-step operator procedures for recurring development and maintenance tasks.
Each document describes who runs the workflow, when, what tools are involved, and what artifacts it produces.

## Documents

| Document | Summary |
|----------|---------|
| [fixture-workflow.md](fixture-workflow.md) | Defines the 3-step operator workflow (1 script + 2 agent ops): verify eligibility (license/language/vendor), health check (CI/tests/activity), gather PRs into staging/candidate-prs.json. Tests clone on demand; nothing is vendored. Keywords: fixture workflow, eligibility, health check, candidate-prs, staging, base_sha. |
| [issue-resolution-team.md](issue-resolution-team.md) | Defines the default 6-role team structure (Architect, Policy Reviewer, Planner, Coder, Reviewer, Tester) used for every issue resolution run; specifies execution order, ambiguity resolution protocol, and escalation rules. Keywords: team structure, issue resolution, architect, policy reviewer, planner, coder, reviewer, tester, orchestration, ambiguity. |
