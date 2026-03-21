# Policy 12 — Worker Management

Workers are the execution layer of the agent_agent system. A worker is a process that claims GitHub issues from a hosted server, runs a DAG against each issue, and reports results — all without direct database access. This policy governs the architectural boundary between workers and the hosted server, the per-issue lifecycle, crash recovery, checkpoint pushes, and the test double. These rules exist because workers operate unattended and must fail safely: the hosted server is the sole coordinator and sole Postgres writer; workers are stateless HTTPS clients that neither share nor persist state locally.

---

## Coordination Model

### P12.1 The hosted server is the sole coordinator and sole Postgres writer

All persistent state lives in Postgres on the hosted server. Workers have no `DATABASE_URL` and no direct database access. Every state write a worker needs — claim creation, status updates, heartbeats, node outputs — is made via HTTPS to the hosted server. Any worker that writes to Postgres directly violates this policy regardless of whether it holds a valid credential.

### P12.2 StateStore is a typed protocol; workers use HTTPStateStore exclusively

`StateStore` is a `typing.Protocol`. The server-side implementation is `SQLAlchemyStateStore`. The worker-side implementation is `HTTPStateStore`, a thin HTTPS client that forwards all reads and writes to the hosted server. Workers MUST instantiate `HTTPStateStore` and MUST NOT instantiate `SQLAlchemyStateStore`. The `Orchestrator` accepts any `StateStore` implementation — this is the extension point, not a bypass for the database-access rule.

### P12.3 Workers require exactly these settings; DATABASE_URL must be absent

Workers MUST be configured with:

| Setting | Description |
|---------|-------------|
| `AGENT_AGENT_SERVER_URL` | Base URL of the hosted server |
| `AGENT_AGENT_SERVER_TOKEN` | Bearer token for HTTPS calls to the hosted server |
| `AGENT_AGENT_GIT_PUSH_ENABLED=true` | Required in all non-dev environments |
| `GITHUB_TOKEN` | Required for repository and issue operations (see below) |

Workers require `GITHUB_TOKEN` for:
- `git clone` of the target repository (if private)
- Opening GitHub PRs after successful DAG execution [P9.2]
- Posting structured escalation comments to GitHub issues [P6.4, P12.9]

`GITHUB_TOKEN` on workers is scoped to repository operations and issue comments. GitHub label management is NOT performed by workers — that is exclusively the server's responsibility [P12.6]. Workers must not call any label mutation API even if their token has the permission.

The server also holds a `GITHUB_TOKEN` for label management. Both holding the token is correct — the responsibilities are distinct.

Workers MUST NOT have `DATABASE_URL` set. If `DATABASE_URL` is present in the environment, the worker MUST refuse to start with a clear error message. If `AGENT_AGENT_GIT_PUSH_ENABLED` is `false` in a non-dev environment, the worker MUST also refuse to start. If `GITHUB_TOKEN` is absent, the worker MUST refuse to start with a clear error message.

---

## Worker Types

### P12.4 Two worker types exist; they share the same per-issue protocol

- **Immediate worker**: persistent process, manually started, polls `GET /claims/available` continuously and handles many issues sequentially. Invoked as `agent-agent worker --id <name>`.
- **Future worker**: ephemeral, handles one issue then exits, spawned by external orchestration. Invoked as `agent-agent worker --id <name> --once [--issue-url <url>]`. If `--issue-url` is provided, the worker skips polling and claims that specific issue directly.

Both types execute each issue using the identical per-issue protocol defined in P12.5–P12.9. The distinction is lifetime only: immediate workers loop; future workers exit after one issue.

---

## Per-Issue Lifecycle

### P12.5 The per-issue lifecycle has six mandatory steps

Every worker — immediate or future — MUST execute the following sequence for each issue:

1. Poll `GET /claims/available` (or use `--issue-url` if provided).
2. `POST /claims` with `{issue_url, worker_id, dag_run_id}`. The server handles atomicity. A 409 response means another worker claimed the issue first; re-poll.
3. `git clone` the target repo into `tempfile.mkdtemp()`. Validate `CLAUDE.md` and the policy index before proceeding.
4. Run `Orchestrator` with `HTTPStateStore` — all state writes go to the hosted server via HTTPS.
5. After each coding node completes, push the branch to remote (checkpoint push — see P12.8).
6. `POST /claims/{issue_url}/release`; `rm -rf` the temp clone.

No step may be skipped. The temp clone MUST be deleted whether the run succeeds, fails, or is abandoned.

### P12.6 Label transitions are server-owned; workers perform no GitHub label operations

| Event | Label change | Responsibility |
|-------|-------------|----------------|
| Claim processed | remove `agent-ready`, add `agent-running` | Server (when it processes `POST /claims`) |
| Run completed | remove `agent-running` | Server (when it processes `POST /claims/{url}/release` with `status=completed`) |
| Stale detected | remove `agent-running`, add `agent-stale` | Server (background task — see P12.10) |
| Human re-queues | add `agent-ready` | Human only — never automated |

Workers are pure DAG executors. They communicate state changes (claim, heartbeat, release) to the server via HTTPS; the server owns all side effects of those state changes, including every GitHub label transition. Workers MUST NOT call any GitHub label mutation API (add or remove any label) directly, even if their `GITHUB_TOKEN` has the permission to do so. Label management is exclusively the server's responsibility.

---

## Heartbeat

### P12.7 Workers send a heartbeat every 60 seconds during execution

During active execution of an issue, the worker MUST send `POST /claims/{issue_url}/heartbeat` every 60 seconds. The purpose of heartbeats is to allow the hosted server to detect dead workers. Workers MUST NOT implement stale detection logic themselves — stale detection is entirely a server responsibility (P12.10).

---

## Stale Detection

### P12.10 Stale detection is a hosted-server responsibility, not a worker responsibility

The hosted server runs a background task every 60 seconds that queries for claims where `last_heartbeat_at < now - 10 minutes AND status = 'running'`. For each stale claim the server:

- Marks `status = stale`.
- Swaps the GitHub label `agent-running → agent-stale`.
- Emits a Level 1 observability event.

Workers MUST NOT scan for stale claims on startup or at any other time. Workers MUST NOT write `status = stale`. This responsibility is entirely server-owned.

---

## Crash Recovery

### P12.11 Crash recovery is triggered by a human re-applying agent-ready; resumption is mandatory

When a human re-applies `agent-ready` after reviewing a stale escalation, the next worker to claim the issue follows this protocol:

1. The claim row contains a non-null `dag_run_id`. The worker MUST treat this as a resumption candidate — never silently start a fresh run.
2. The worker checks whether the `branch_name` from `dag_nodes` exists on the remote using `git ls-remote`.
3. **Branch exists**: checkout a fresh clone from the remote branch, reset all `status=running` nodes to `pending`, and resume from the last completed node. Completed node outputs are never discarded [P6.5a].
4. **Branch absent**: escalate via `github_comment`, then release the claim as `failed`.

Silently starting a fresh run when `dag_run_id` is non-null is a violation of this policy and of P6.5a. Resumption is node-level only — a crashed node is reset to `pending` and re-executed from the beginning; there is no mid-node resumption.

---

## Checkpoint Pushes

### P12.8 Workers must push a checkpoint branch to remote after each coding node completes

After each coding node completes successfully, the worker MUST push the current branch to the remote. This checkpoint is the recovery surface used by P12.11 crash recovery: without it, a remote branch does not exist and the crashed run cannot be resumed.

- Guarded by `git_push_enabled`. In non-dev environments, `git_push_enabled=false` is a misconfiguration; workers refuse to start [P12.3].
- On push failure: retry 3× with exponential backoff.
- On persistent push failure: escalate via `github_comment` with a message that clearly states "branch not pushed; crash recovery degraded". Do not treat a failed checkpoint push as a silent warning.

---

## Escalation Channel

### P12.9 Workers must use the github_comment escalation channel unconditionally

`github_comment` is the **only** valid escalation channel in worker mode — not a default, not a preference, the sole permitted channel. The CLI escalation channel is a separate path for interactive `agent-agent run` usage (with a terminal attached); it does not apply to workers at all.

If `AGENT_AGENT_ESCALATION_CHANNEL=cli` is set in the environment, the worker MUST silently override it to `github_comment` and emit a WARNING-level structlog record. This is not a startup error — silent correction is preferred over blocking the worker.

The worker escalation model is **fire-and-release**: post the `github_comment`, then release the claim. Workers MUST NOT pause and poll for a human response after posting an escalation comment. See P6 for the full worker escalation model.

---

## Integration Test Double

### P12.12 The in-process FastAPI server is the canonical integration test double

Integration tests MUST spin up an in-process FastAPI server using the same `server.py` and `SQLAlchemyStateStore` with an `:memory:` SQLite database, then point `HTTPStateStore` at it. This is the canonical integration test path. HTTP calls MUST NOT be mocked in integration tests — the `HTTPStateStore` ↔ in-process server round-trip is what the tests verify. Unit tests may mock `StateStore` directly via the protocol.

---

### Violations

- A worker that has `DATABASE_URL` set or writes to Postgres directly [P12.1]
- A worker that instantiates `SQLAlchemyStateStore` [P12.2]
- A worker that starts when `DATABASE_URL` is present in the environment [P12.3]
- A worker that starts when `AGENT_AGENT_GIT_PUSH_ENABLED=false` in a non-dev environment [P12.3]
- Skipping any of the six per-issue lifecycle steps, including temp-clone deletion [P12.5]
- A worker performing any GitHub label operation (applying or removing any label) [P12.6]
- Human automation re-applying `agent-ready` after stale detection [P12.6]
- A worker that does not send heartbeats during execution [P12.7]
- A worker that implements stale detection logic [P12.10]
- A worker that writes `status = stale` [P12.10]
- Starting a fresh run when `dag_run_id` is non-null without attempting resumption [P12.11, P6.5a]
- Discarding completed node outputs during resumption [P12.11, P6.5a]
- Performing mid-node resumption instead of resetting crashed nodes to `pending` [P12.11]
- Not pushing a checkpoint branch after each coding node completes [P12.8]
- Silently swallowing a persistent checkpoint push failure instead of escalating [P12.8]
- Using the CLI escalation channel in worker mode [P12.9]
- Pausing a worker and polling for a human GitHub comment response instead of fire-and-release [P12.9, P6 worker escalation]
- A worker calling any GitHub label mutation API (add/remove label), even if `GITHUB_TOKEN` permits it [P12.6]
- A worker missing `GITHUB_TOKEN` attempting to run anyway [P12.3]
- Mocking HTTP calls in integration tests instead of using the in-process server [P12.12]

### Quick Reference

| Rule | Requirement |
|------|-------------|
| P12.1 | Hosted server is sole coordinator and sole Postgres writer; workers have no `DATABASE_URL` |
| P12.2 | Workers use `HTTPStateStore` exclusively; `SQLAlchemyStateStore` is server-only |
| P12.3 | Workers require `SERVER_URL`, `SERVER_TOKEN`, `GIT_PUSH_ENABLED=true`, `GITHUB_TOKEN`; `DATABASE_URL` must be absent; missing `GITHUB_TOKEN` is a startup error |
| P12.4 | Two worker types (immediate / future); identical per-issue protocol; differ only in lifetime |
| P12.5 | Six-step per-issue lifecycle: poll → claim (409 = re-poll) → clone+validate → run with HTTPStateStore → checkpoint push → release+cleanup |
| P12.6 | Server owns all label transitions: removes `agent-ready`/adds `agent-running` on claim, removes `agent-running` on completion, swaps `agent-running→agent-stale` on stale detection; human re-queues manually; workers perform no label operations |
| P12.7 | Heartbeat every 60s via `POST /claims/{issue_url}/heartbeat`; stale detection is server-only |
| P12.10 | Server background task every 60s detects `last_heartbeat_at < now - 10min`; marks stale, swaps label, emits event |
| P12.11 | Non-null `dag_run_id` → mandatory resumption attempt; check remote branch; resume or escalate+fail; no silent fresh runs |
| P12.8 | Push checkpoint branch after each coding node; retry 3× on failure; persistent failure escalates with "crash recovery degraded" |
| P12.9 | `github_comment` only — sole permitted channel, not a default; CLI channel not applicable to workers; if `AGENT_AGENT_ESCALATION_CHANNEL=cli` is set, silently override to `github_comment` and emit WARNING; fire-and-release model: post comment then release claim, do not poll for response |
| P12.12 | Integration tests use in-process FastAPI + SQLite `:memory:`; no HTTP mocking |
