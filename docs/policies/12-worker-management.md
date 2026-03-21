# Policy 12: Worker Management

A worker is a long-running process that autonomously claims GitHub issues from a human-curated queue, executes DAG runs against them, and reports results. Workers are the execution substrate for the semi-autonomous self-improvement milestone: humans dispatch work by applying labels; workers claim, execute, and report without further human involvement until the PR review checkpoint [P9.2]. This policy governs the complete worker lifecycle — identity, claim coordination, issue eligibility, budget assignment, and observability — as a single closed constraint set. These rules exist because the worker layer introduces new coordination problems (concurrent claiming, crash recovery, mid-run cancellation) that existing policies do not cover, and because workers operate unattended: mistakes compound silently rather than being caught at an interactive checkpoint.

---

## Identity and Registration

### P12.1 Worker ID is generated at process start and is non-configurable

Every `agent-agent worker` process generates a `worker_id` via `uuid4()` on startup. The `worker_id` is never read from configuration, environment variables, or persistent storage, and is never reused across restarts. If the same container restarts, it gets a new `worker_id`. This ensures that a claim held by a crashed process cannot be silently re-adopted under the same identity.

### P12.2 Workers write a registration record before polling begins

Before the first poll cycle, a worker MUST write a registration record containing exactly these fields:

| Field | Type | Description |
|-------|------|-------------|
| `worker_id` | UUID | Generated at startup [P12.1] |
| `hostname` | str | `socket.gethostname()` |
| `pid` | int | `os.getpid()` |
| `started_at` | timestamptz | Process start time |
| `version` | str | `agent_agent.__version__` |
| `mode` | `"continuous" \| "once"` | Whether the worker loops or exits after one claim |

Fields not in this list require a policy amendment before addition. This is not a freeze against legitimate operational needs — it is a forcing function to ensure additions are deliberate. Fields MUST NOT carry per-issue or per-run state; that belongs in `work_claims` and `dag_runs`.

### P12.3 Staleness is defined by a fixed formula and is computed at read time only

Workers update `last_heartbeat_at` in their active `work_claims` row at a fixed interval defined in config (`WORKER_HEARTBEAT_INTERVAL_SECONDS`, default 30 s). A claim is stale when:

```
now() - last_heartbeat_at > 5 * WORKER_HEARTBEAT_INTERVAL_SECONDS
```

This formula is the **sole definition of stale**. The 5× multiplier matches the lower bound of production job queue systems (Solid Queue: 5×, Sidekiq: 6×, pg-boss: 6×); the commonly cited 3× floor is too aggressive for containerised environments where a slow database write or network jitter can delay a heartbeat without indicating a dead worker.

Staleness is a read-time computation evaluated by the querying component (bookkeeping server, `agent-agent status` command, a worker polling for candidates). No process writes `stale` to `work_claims.status` — the written statuses are defined in the next section.

---

## Claim Lifecycle

### P12.4 The work_claims state machine has four written statuses

The `status` column in `work_claims` accepts exactly four written values: `running`, `completed`, `failed`, `abandoned`. The legal state machine:

```
          [initial claim]
                │
                ▼
             running ◄────────────────────────────────────────┐
                │                                             │
     ┌──────────┼──────────────┐                   (reclaim after stale:
     ▼          ▼              ▼                    conditional UPDATE)
 completed    failed       abandoned
```

Transitions not shown are illegal — in particular, all terminal states (`completed`, `failed`, `abandoned`) are irreversible. A stale claim (by the formula in P12.3) is reclaimed via a conditional UPDATE that resets `worker_id`, `claimed_at`, and `last_heartbeat_at` to the new worker's values and sets `status = 'running'`. This is not a new INSERT — the row persists to preserve the `dag_run_id` reference for crash recovery [P12.7].

### P12.5 The holding worker process is the sole writer to work_claims.status

The following components MUST NOT write to `work_claims.status`: agent composites, the bookkeeping server, the `agent-agent status` CLI command, background jobs, or any process that is not the worker process holding the claim. This applies even if those components have database credentials. The sole writers are the worker holding the claim, and any worker performing a reclaim on a stale row.

### P12.6 A claim transitions to completed only after the GitHub PR URL is confirmed

The required sequence:
1. DAG run reaches terminal `completed` state
2. Orchestrator opens the GitHub PR [P9.2]
3. PR URL is written to `dag_runs`
4. Worker sets `work_claims.status = 'completed'`

If step 2 or 3 fails after a completed DAG run, the claim remains `running` and the failure is escalated as a deterministic error [P6.1c]. Marking a claim `completed` before the PR URL is in `dag_runs` is a violation regardless of DAG run outcome.

### P12.7 Reclaiming a stale claim requires a resumption check before starting a new DAG run

When a worker reclaims a stale claim, it MUST check the existing `dag_run_id` before starting a new DAG run:

- **Resumable** (existing run is non-terminal and the worktree can be reconstructed): resume from the last completed node. Completed node outputs are never discarded [P6.5a].
- **Not resumable** (worktree lost, state corrupt, or run already terminal): transition the existing run to `failed`, post a `github_comment` escalation with partial results [P6.5b–c], then start a fresh DAG run under a new `dag_run_id`.

Starting a fresh run without attempting resumption first is a P6.5a violation.

> **Intentional divergence from industry norm.** Production job queue systems (Sidekiq, Celery, Oban, pg-boss) universally use *restart* semantics after a worker crash: the job is requeued and re-executed from the beginning. Resumption is rare because it requires persistent intermediate state. This policy requires resumption-first because P6.5a prohibits discarding completed node outputs, and a DAG run may have completed expensive Plan or Coding composite nodes before the worker crashed. The cost of implementing resumption is accepted in exchange for not re-billing and re-executing work that already succeeded. If resumption proves too complex to implement reliably, a policy amendment is required — do not silently downgrade to restart semantics.

### P12.8 The abandoned state covers all mid-run human or environmental interruptions

A worker MUST transition a claim to `abandoned` — and MUST NOT start any new DAG node — when any of the following are detected during the heartbeat cycle:

| Trigger | Detection |
|---------|-----------|
| Human cancellation | `agent-running` label absent from issue |
| Issue closed | GitHub API reports issue `state = 'closed'` |
| Target repo unreachable | GitHub API returns 404 or 403 for the repo |

On detecting any abandonment trigger the worker MUST:

1. Complete the current atomic sub-operation (a git commit, a file write, an in-flight SDK call) but MUST NOT start the next DAG node.
2. Post a `github_comment` escalation documenting: the trigger, which nodes completed, which were in-progress, and any partial results [P6.5].
3. Remove `agent-running` label if present; apply `agent-stale` label.
4. Set `work_claims.status = 'abandoned'` and exit.

`abandoned` is terminal. Re-queuing requires a human to remove `agent-stale`, review the escalation, and re-apply `agent-ready`.

---

## Issue Eligibility and Budget

### P12.9 A worker MUST pass all eligibility checks before attempting a claim

Before any claim attempt, a worker MUST verify all of the following. Failure on any check produces a `worker.claim_skipped` event [P12.13] with an enumerated `skip_reason` — not an escalation. Eligibility checks are read-only.

| Check | Pass condition |
|-------|---------------|
| Label state | Issue has `agent-ready`; does NOT have `agent-running` or `agent-stale` |
| No active claim | No row in `work_claims` for this `issue_url` with `status = 'running'` and `last_heartbeat_at` within the stale threshold [P12.3] |
| No open agent PR | No open PR with branch prefix `agent/<issue-number>/` on the repo |
| Issue is open | GitHub API reports issue `state = 'open'` |
| Branch writable | GitHub Collaborator Permission API (`GET /repos/{owner}/{repo}/collaborators/{username}/permission`) confirms at least `write` access. Dry-run pushes MUST NOT be used for this check. |

### P12.10 After a successful claim, the worker MUST re-run all eligibility checks before starting a DAG run

The pre-claim checks in P12.9 have a TOCTOU window between the check and the atomic INSERT. After a successful claim, the worker MUST re-run every P12.9 eligibility check before starting the DAG run. If any check fails:

- Transition claim to `abandoned`
- Do not start a DAG run
- Do not post a GitHub escalation comment (this is a pre-run eligibility failure, not a mid-run disruption)
- Emit `worker.claim_abandoned` with `abandon_reason` set to the failing check's `SkipReason` value suffixed with `"_post_claim"`

This three-step pattern — pre-claim filter → atomic claim → post-claim recheck — combines the efficiency of filtering obvious non-starters before claiming with the correctness of catching state changes in the TOCTOU window. It requires no additional state and produces no claiming loops: persistent failure conditions are caught in the pre-claim filter and never result in a claim; transient window failures are caught post-claim and produce a terminal `abandoned` state.

### P12.11 A worker MUST NOT claim a running, non-stale issue

A worker MUST skip any issue whose `work_claims` row has `status = 'running'` and is not yet stale per P12.3. This applies unconditionally — `worker_id` matching between the current worker and the claimant is irrelevant to eligibility.

### P12.12 Budget is set by configuration; the worker MUST NOT compute it

The worker reads `WORKER_RUN_BUDGET_USD` from the active configuration profile and passes it unchanged as `budget_usd` to the `DAGRun` constructor. This preserves the P7.1 invariant that budget is set at run creation by human-controlled configuration. If `WORKER_RUN_BUDGET_USD` is absent, the worker MUST exit with a non-zero status — no code-level default is permitted. Budget increases during a run follow the escalation path [P7.1, P6.1b].

Per-repo worker concurrency is capped at `WORKER_MAX_CONCURRENT_PER_REPO` (default: 2; configurable). This default reflects GitHub's secondary rate limits for repository write operations and the `agent/<issue-number>/` branch namespace. The cap MUST be enforced atomically as part of the claim operation itself — a subquery count checked inside the claiming CTE — not as a separate pre-claim read. A pre-claim count followed by a separate claim write has a TOCTOU race where two workers simultaneously read N-1 active claims and both proceed, briefly exceeding the cap. When the cap blocks a claim, the worker emits one `worker.repo_concurrency_limit_reached` event for that repo and skips all remaining candidates from it in the current poll cycle.

---

## Observability

### P12.13 Worker events use the same emit_event path as DAG events

Worker events MUST be emitted via the `emit_event` call defined in P11 P1 — not a separate logging path. P11 P3 requires `dag_run_id` and `node_id` on every record; this policy extends that rule: worker events that predate a successful claim use `dag_run_id: null` and `node_id: null`. Once a claim is acquired, all subsequent events for that claim MUST include the assigned `dag_run_id`.

`worker_id` is mandatory on every worker event. It is bound to the `structlog` context at startup, before the registration record is written, and propagates automatically to all log records for the worker's lifetime.

### P12.14 The following worker events are mandatory

| Event name | When emitted | Required extra fields |
|------------|-------------|----------------------|
| `worker.started` | After registration record written | `mode`, `version`, `hostname`, `pid` |
| `worker.claim_attempted` | Before each claim attempt | `issue_url` |
| `worker.claim_acquired` | After successful claim | `issue_url`, `dag_run_id` |
| `worker.claim_skipped` | When eligibility check fails [P12.9] | `issue_url`, `skip_reason` |
| `worker.repo_concurrency_limit_reached` | When per-repo cap hit [P12.12] | `repo_url`, `active_claim_count` |
| `worker.heartbeat` | On each heartbeat write | `issue_url`, `dag_run_id`, `dag_status` |
| `worker.label_transition` | When worker applies a label change | `issue_url`, `from_label`, `to_label` |
| `worker.claim_completed` | Claim → `completed` | `issue_url`, `dag_run_id`, `pr_url` |
| `worker.claim_failed` | Claim → `failed` | `issue_url`, `dag_run_id`, `failure_reason` |
| `worker.claim_abandoned` | Claim → `abandoned` | `issue_url`, `dag_run_id`, `abandon_reason` |
| `worker.stopped` | On clean shutdown | `claims_completed`, `claims_failed`, `claims_abandoned` |

**Poll-cycle events are not emitted.** High-frequency per-cycle events (e.g., `worker.poll_cycle`) generate low-signal noise at Level 2 retention volume. The per-candidate events above (`worker.claim_attempted`, `worker.claim_skipped`, `worker.repo_concurrency_limit_reached`) capture poll-cycle outcomes with signal. A cycle that finds zero eligible candidates produces no events.

The `skip_reason` field in `worker.claim_skipped` MUST be a value from the `SkipReason` enum in `models/worker.py`. Permitted values:

| Value | Meaning |
|-------|---------|
| `active_claim_exists` | Non-stale running claim already held |
| `label_state_invalid` | `agent-ready` absent or blocking label present |
| `open_agent_pr_exists` | Open PR with matching branch prefix already exists |
| `issue_not_open` | Issue state is not `open` |
| `insufficient_repo_permissions` | Worker token lacks write access |

The enum MUST NOT include a catch-all (`OTHER`, `UNKNOWN`, or equivalent). New skip conditions require a policy amendment to add a new enum value.

### P12.15 Worker events share the DAG event table and follow P11 retention rules

Worker events are Level 2 (metrics) data stored in the same `node_events` table as DAG events — not a separate table or log file. They are retained for 90 days consistent with P11. Worker events are distinguished from DAG events by `dag_run_id: null` (pre-claim) and by the `worker.*` event name prefix.

The bookkeeping server MUST expose:

```
GET /api/v1/workers
```
Non-stale workers (heartbeat within `5 * WORKER_HEARTBEAT_INTERVAL_SECONDS`) with their current claim. Computable from `work_claims` without a background aggregation job.

```
GET /api/v1/workers/{worker_id}/events
```
Chronological `worker.*` events for the given worker. Supports `?since=` and `?limit=`. Primary diagnostic surface for "why isn't this issue being claimed?"

```
GET /api/v1/queue/age
```
Age in seconds of the oldest `agent-ready` issue that has no active non-stale claim. This is the single most important leading indicator of worker health — it rises before queue depth does and distinguishes "workers are busy" from "workers are broken." An alert threshold on this value (e.g., P99 age > 10 minutes) provides earlier warning than alerting on queue depth alone.

These endpoints are provisional — a dedicated bookkeeping server API policy will supersede this rule when the server's full API surface is governed.

---

### Violations

- Configuring or reusing `worker_id` across restarts [P12.1]
- Polling before the registration record is written [P12.2]
- Adding registration fields without a policy amendment [P12.2]
- Any process other than the staleness formula writing `stale` to `work_claims.status` [P12.3]
- Writing any status value other than `running`, `completed`, `failed`, `abandoned` [P12.4]
- Inserting a new row for a reclaim instead of using conditional UPDATE [P12.4]
- Agent composites, bookkeeping server, CLI, or background jobs writing to `work_claims.status` [P12.5]
- Setting `completed` before PR URL is confirmed in `dag_runs` [P12.6]
- Starting a fresh DAG run after reclaiming a stale claim without attempting resumption [P12.7, P6.5a]
- Starting a new DAG node after detecting an abandonment trigger [P12.8]
- Using CLI escalation channel in worker mode [P12.8, P6.6]
- Skipping any P12.9 eligibility check before a claim attempt [P12.9]
- Using a dry-run push to verify branch writability [P12.9]
- Treating a failed eligibility check as an escalation [P12.9]
- Starting a DAG run without re-running all P12.9 eligibility checks after claim [P12.10]
- Posting a GitHub escalation comment for a pre-run eligibility failure detected post-claim [P12.10]
- Claiming a non-stale running issue [P12.11]
- Computing or hardcoding a budget value; falling back when `WORKER_RUN_BUDGET_USD` is unset [P12.12]
- Claiming beyond the per-repo concurrency cap [P12.12]
- Emitting worker events via a separate logging path [P12.13]
- Omitting `worker_id` from any worker event [P12.13]
- Omitting `dag_run_id` from a worker event that has an active claim [P12.13]
- Omitting any mandatory event from P12.14 [P12.14]
- Emitting high-frequency poll-cycle events [P12.14]
- Using free-text or a catch-all enum value for `skip_reason` [P12.14]
- Storing worker events in a separate table or log file [P12.15]

### Quick Reference

| Rule | Requirement |
|------|-------------|
| P12.1–2 | `worker_id` = `uuid4()`, never reused; registration record before first poll |
| P12.3 | Stale = `5 × WORKER_HEARTBEAT_INTERVAL_SECONDS`; read-time only; never written |
| P12.4 | Four written statuses; reclaims use conditional UPDATE |
| P12.5 | Worker process is sole writer to `work_claims.status` |
| P12.6 | `completed` requires confirmed PR URL in `dag_runs` |
| P12.7 | Reclaim → resumption check first; fresh run only if not resumable |
| P12.8 | Abandonment triggers: label removed, issue closed, repo unreachable; stop at next node boundary |
| P12.9 | Five eligibility checks; failures → skip events, not escalations; permissions API for branch check |
| P12.10 | Re-run all P12.9 checks after claim; any failure → `abandoned`, no DAG run, no escalation comment |
| P12.11 | Non-stale running claim blocks all workers unconditionally |
| P12.12 | Budget from `WORKER_RUN_BUDGET_USD` only; unset → refuse to start; per-repo cap default 2; cap enforced atomically in claim CTE |
| P12.13 | `emit_event` path; `worker_id` bound at startup; `dag_run_id: null` permitted pre-claim |
| P12.14 | 11 mandatory events; no poll-cycle events; `SkipReason` enum, no catch-alls |
| P12.15 | Level 2; 90-day retention; same table as DAG events; three bookkeeping endpoints including queue age |
