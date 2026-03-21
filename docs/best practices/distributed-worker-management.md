# Best Practices for Distributed Worker Management Systems

This document covers how production systems solve the core problems of distributed worker coordination: identity, claiming, liveness, crash recovery, eligibility, concurrency, and observability. Each section names a concrete pattern, describes the problem it solves, explains the mechanics with examples from real systems, and notes trade-offs and applicability.

---

## BP-1: Skip-Locked Atomic Claiming

**Problem it solves:** When multiple workers poll a shared job table simultaneously, naive `SELECT` + `UPDATE` sequences produce race conditions: two workers read the same "pending" row, both attempt to claim it, and one wins while the other either double-processes or hits a unique-constraint error. Advisory locks and application-level mutexes serialize all workers through a single gate, killing throughput.

**How it works:**

PostgreSQL 9.5 introduced `SKIP LOCKED`, which causes a `SELECT FOR UPDATE` to silently skip any row already held by another transaction rather than blocking. The result is that each concurrent worker gets a distinct row without coordination overhead. A canonical single-statement claim looks like this:

```sql
WITH claimed AS (
  SELECT id FROM jobs
  WHERE status = 'pending'
  ORDER BY priority ASC, id ASC
  LIMIT 1
  FOR UPDATE SKIP LOCKED
)
UPDATE jobs
SET status = 'running', claimed_at = now(), worker_id = $1
FROM claimed
WHERE jobs.id = claimed.id
RETURNING *;
```

Because the `SELECT` and `UPDATE` happen in the same statement inside a CTE, no other transaction can interleave between the read and the write. The lock is held only for the duration of the statement, so throughput scales with worker count.

**Real-world usage:**

- **Oban (Elixir):** Uses `FOR UPDATE SKIP LOCKED` as its primary claiming mechanism against a PostgreSQL `oban_jobs` table. Jobs transition from `available` → `executing` atomically at claim time.
- **Solid Queue (Rails/37signals):** Runs two query forms — an unfiltered poll (`SELECT job_id FROM solid_queue_ready_executions ORDER BY priority ASC, job_id ASC LIMIT ? FOR UPDATE SKIP LOCKED`) and a single-queue form that adds a `WHERE queue_name = ?` predicate — so workers assigned to specific queues do not compete with general-purpose workers.
- **River (Go):** Produces a "producer inside each process" that consolidates SKIP LOCKED fetches for all internal goroutine executors, reducing the number of round trips to the database while still providing per-row atomic claims.
- **pg-boss (Node.js):** Explicitly documents that it "relies on PostgreSQL's SKIP LOCKED feature built specifically for message queues to provide exactly-once delivery and guaranteed atomic commits."
- **Que (Ruby):** One of the earliest adopters of this pattern in a Ruby ecosystem library.

**Trade-offs:**

- *Inconsistent snapshot:* `SKIP LOCKED` intentionally provides a non-serializable view. Rows being skipped are invisible. This is correct behavior for a queue but would be wrong for financial reporting.
- *Index dependency:* Performance depends entirely on a partial index covering the `status = 'pending'` (or equivalent) rows and the ordering columns. Without it, full table scans under load cause latency spikes.
- *Postgres-only:* The feature exists in MySQL 8+ as well but is absent from SQLite, meaning this pattern is database-specific.
- *No cross-node backpressure:* SKIP LOCKED claims are greedy; if a worker can claim it will. Concurrency limits must be enforced at a higher level (see BP-6).

**When to use:** Any PostgreSQL-backed job queue where you want atomic claiming without external lock services. Scales well to hundreds of concurrent workers.

**When not to use:** Cross-database systems, situations requiring a globally consistent view of queue state, or workloads where job eligibility depends on factors that cannot be expressed as indexed predicates (see BP-5 for that problem).

---

## BP-2: Visibility-Timeout / Lease-Based Claiming

**Problem it solves:** Broker-based queues (no shared relational database) need crash recovery without requiring workers to explicitly delete messages they are processing. If a worker dies, the message must automatically become available again — but you cannot rely on the worker to signal its own death.

**How it works:**

When a consumer receives a message, the broker marks it invisible to all other consumers for a configurable *visibility timeout* (SQS term) or *lock duration* (Azure Service Bus term). The consumer must either (a) delete/acknowledge the message before the timeout expires, signalling success, or (b) do nothing, in which case the timeout expires and the message reappears in the queue for another consumer to claim.

This means crash recovery requires *zero coordination*: the broker handles it by simply waiting. There is no heartbeat or explicit failure signal needed.

**Extending the lease during long processing:**

Both SQS and Azure Service Bus provide APIs to extend the timeout while work is in progress — the worker periodically calls the extension API as a heartbeat:

- **SQS:** `ChangeMessageVisibility(ReceiptHandle, VisibilityTimeout)`. Default timeout: 30 seconds. Maximum from initial receipt: 12 hours. AWS recommends setting the queue's default visibility timeout to at least 6× the Lambda function timeout.
- **Azure Service Bus (Peek-Lock mode):** `RenewMessageLockAsync()`. Default lock duration: 1 minute. Maximum configurable lock duration: 5 minutes (longer durations require periodic renewal). The SDK provides an automatic lock-renewal feature that handles this in the background.

**Real-world usage:**

- **Amazon SQS Standard Queues:** Deliver messages at-least-once. The visibility timeout is the only crash recovery mechanism; there are no explicit heartbeats. Dead-letter queues (DLQ) capture messages that have exceeded a configurable `maxReceiveCount`.
- **Azure Service Bus PeekLock:** Workers receive a message but do not remove it from the queue. They call `Complete()` on success, `Abandon()` to release immediately, or `DeadLetter()` to send the message to the dead-letter subqueue. Lock expiry without any of these calls triggers redelivery.
- **Google Cloud Tasks:** Guarantees at-least-once delivery by retrying tasks until a success HTTP response is received from the target handler, with configurable retry schedules and deadlines.
- **Faktory:** Uses a `reserve_for` timeout (default 1800 seconds / 30 minutes). If the worker does not send `ACK` or `FAIL` within that window, the job is released for re-execution.

**Trade-offs:**

- *At-least-once only:* Because the message can reappear after timeout expiry even if the worker completed successfully but failed to delete before the timeout, duplicate delivery is possible. Workers must be idempotent (see BP-4).
- *Timeout tuning is hard:* Too short → jobs re-queue before they finish, causing duplicates and wasted work. Too long → failed jobs are invisible for a long time, delaying retry. Neither SQS nor Service Bus allows a per-message timeout at enqueue time without application-level workarounds.
- *Extension adds coupling:* The heartbeat-via-extension pattern requires the worker process to call back to the broker while processing. If the worker is CPU-saturated (e.g., running an ML inference loop), the extension call may not fire in time.

**When to use:** Cloud-native architectures using managed broker services; heterogeneous worker fleets where workers speak different languages; workloads tolerant of at-least-once semantics.

**When not to use:** Workloads where exactly-once execution is critical and full idempotency cannot be achieved; systems where job re-visibility after a crash must be faster than the minimum configurable timeout.

---

## BP-3: Heartbeat with TTL-Based Dead Worker Detection

**Problem it solves:** In systems where workers hold long-running jobs, the coordinator must detect that a worker has died and reclaim its jobs. Polling each worker directly is not feasible in large clusters. The solution is for each worker to periodically write a liveness record with a short TTL; if the write stops, the record expires and the coordinator detects the gap.

**How it works:**

Each worker runs a background thread or goroutine that writes a heartbeat record on a fixed interval. The record carries a TTL equal to some multiple of the interval. The coordinator (or a separate reaper process) scans for workers whose heartbeat has expired and marks their in-flight jobs as abandoned or failed.

**Intervals and thresholds found in real systems:**

| System | Heartbeat interval | Staleness threshold | Ratio |
|---|---|---|---|
| Sidekiq | 10 seconds | 60 seconds (Redis TTL) | 6× |
| Celery | 2 seconds (broker heartbeat check rate is 2× the value) | ~120 seconds (worker considered offline) | ~10× |
| Oban Lifeline plugin | 1 second | 60 seconds (minimum `beats_maxage` before rescuing orphaned jobs) | 60× |
| Solid Queue | 60 seconds | 300 seconds (5 minutes) | 5× |
| Faktory | 10–15 seconds | 60 seconds | 4–6× |
| pg-boss | 5 seconds | 30 seconds | 6× |
| BullMQ | `lockDuration / 2` (lock renewal) | `lockDuration` (default 30 seconds) | 2× |

The "3× rule" commonly cited in distributed systems literature is a floor; most production systems use 4–6× to absorb network jitter and transient slow writes. Oban's 60× ratio reflects the fact that its heartbeat is written to Postgres (not Redis) and the rescue scan runs every 60 seconds regardless of beat frequency.

**Concrete mechanics by system:**

- **Sidekiq:** A dedicated heartbeat thread writes process metadata (hostname, PID, concurrency, busy count, RSS) to a Redis hash with a 60-second expiry every 10 seconds. The key format is `processes:hostname:pid:uuid`. If the hash is not found on lookup, the process is considered dead. In-flight jobs for dead processes are bulk-requeued via `bulk_requeue`.
- **Oban Lifeline:** Writes a row to `oban_beats` every second. A rescue task runs every 60 seconds, scanning jobs in `executing` state whose `attempted_by` node+queue has no recent beat entry. Those jobs are transitioned back to `available`. `beats_maxage` defaults to 300 seconds and controls how long beat rows are retained.
- **Solid Queue:** Configurable `process_heartbeat_interval` (default 60s) and `process_alive_threshold` (default 300s). The supervisor prunes processes exceeding the threshold. Jobs claimed by pruned processes are marked failed with `SolidQueue::Processes::ProcessPrunedError`.
- **Faktory:** Workers send `BEAT {"wid":"...", "rss_kb":...}` every 10–15 seconds over a persistent TCP connection to the Faktory server. After 60 seconds without a beat, Faktory removes the worker from its Busy page. The server can also piggyback control signals (`quiet`, `terminate`) in the heartbeat response.
- **BullMQ:** Uses lock renewal as its heartbeat. A worker holds a Redis lock for each active job and renews it every `lockDuration / 2` milliseconds. If the NodeJS event loop is saturated (CPU-blocking code), renewal fails, the lock expires, and the job is moved to a stalled set. A `StalledChecker` running at `stalledInterval` (default 30 seconds) detects these and returns them to the waiting state.

**Trade-offs:**

- *Shorter intervals = faster detection but more load.* Oban's 1-second beat writes to Postgres — this is only feasible because Postgres handles small writes efficiently and the row uses `INSERT ... ON CONFLICT DO UPDATE` (upsert).
- *Longer thresholds = more delayed recovery.* Solid Queue's 5-minute threshold means a crashed worker leaves its jobs invisible for up to 5 minutes before the supervisor intervenes.
- *False positives:* A slow network or a brief database stall can cause a healthy worker to miss a heartbeat write, triggering false "dead" detection. Setting the threshold to at least 3–4× the interval provides a buffer.

**When to use:** Any system where workers hold exclusive claims on jobs for longer than a few seconds. Essential for Postgres-backed queues where there is no broker-managed visibility timeout.

**When not to use:** Very short jobs (sub-second) where the overhead of heartbeat writes exceeds the value; systems using the visibility timeout model (BP-2) which handles this at the broker layer.

---

## BP-4: At-Least-Once Delivery with Idempotent Handlers

**Problem it solves:** In any distributed system, exactly-once delivery is theoretically impossible to guarantee end-to-end: the worker can complete its work but fail before acknowledging, causing the broker to redeliver. The practical solution is to accept at-least-once delivery and design handlers to be safe under duplicate execution.

**How it works:**

The at-least-once contract works as follows:

1. A job is delivered to a worker and acknowledged only after the worker signals success.
2. If the worker crashes before acknowledging, the broker or coordinator redelivers the job (possibly to a different worker).
3. Handlers are designed so that executing the same job twice produces the same observable outcome as executing it once.

**Idempotency strategies:**

- **Natural idempotency:** The operation is inherently safe to repeat (e.g., setting a field to a fixed value, sending a read-only query). No additional machinery needed.
- **Database unique constraint:** The job's side effect inserts a record with a unique constraint keyed on `job_id`. The second execution hits the constraint and either errors (handled) or uses `INSERT ... ON CONFLICT DO NOTHING`.
- **Deduplication table:** A separate `processed_jobs` table records completed job IDs. The handler checks this table first; if the job is already present, it returns success without re-executing.
- **Idempotency key passed to external APIs:** Stripe, Twilio, and other APIs accept client-supplied idempotency keys. Passing the job ID as the idempotency key ensures that two calls for the same job produce only one external side effect.
- **Message-ID deduplication (Azure Service Bus / SQS FIFO):** The broker itself deduplicates within a time window. SQS FIFO queues deduplicate on `MessageDeduplicationId` within a 5-minute window.

**Real-world crash recovery mechanics:**

- **Celery with `task_acks_late=True`:** Task acknowledgment is deferred until after the handler returns. If the worker process receives SIGKILL during execution, the broker redelivers. However, `task_reject_on_worker_lost` must also be set, otherwise a process killed by the OOM killer will acknowledge the task silently and lose it.
- **Sidekiq (OSS):** Uses Redis `BRPOP`, which removes the job from the queue *at the moment of fetch*. A SIGKILL with no prior graceful shutdown loses the job. The `bulk_requeue` mechanism during graceful shutdown returns in-progress jobs, but only if the process has time to run it. Sidekiq Pro adds a SuperFetch mechanism that keeps jobs in a "working" set and requeues from there on death.
- **SQS Standard Queues:** Inherently at-least-once; the visibility timeout is the recovery mechanism. No worker-side acks needed — deletion of the message is the ack. SQS FIFO adds a 5-minute exactly-once deduplication window.
- **Azure Service Bus:** `Complete()` = ack and delete. `Abandon()` = release for immediate redelivery. Lock expiry without settlement = automatic redelivery after timeout.

**Exactly-once effects vs. exactly-once delivery:**

The distinction matters in practice. Tyler Treat's essay "You Cannot Have Exactly-Once Delivery" argues that the guarantee cannot be made at the messaging layer alone because the worker's side effect and the acknowledgment are two separate operations. The tractable engineering goal is: *exactly-once effects* = at-least-once delivery + idempotent handlers. Apache Kafka's transactional producer achieves this within the Kafka ecosystem by making the producer's write and the offset commit atomic, but cross-system exactly-once (e.g., writing to a database and acknowledging a Kafka message atomically) still requires application-level idempotency.

**Trade-offs:**

- *Idempotency adds complexity:* Writing an idempotent handler requires understanding all side effects of the job, including those to external APIs.
- *Deduplication tables add latency:* Every job execution requires a read from the deduplication table before doing any work.
- *At-most-once is sometimes correct:* For analytics events, metrics emission, or non-critical notifications, losing a duplicate is preferable to the complexity of idempotency machinery. Choose the delivery model that matches the use case.

**When to use:** Any workload where a job's side effects are not naturally idempotent and duplicate execution causes incorrect state (double-charging, double-sending emails, double-crediting accounts).

**When not to use:** Simple read-only fan-out where duplicate delivery is harmless; or where the retry cost exceeds the cost of occasional data loss (e.g., fire-and-forget analytics pings).

---

## BP-5: Atomic Eligibility Enforcement at Claim Time

**Problem it solves:** Many jobs have eligibility conditions beyond "is this job in a pending state?" — e.g., "only process this job if the account's balance is above zero" or "only send this notification if the user has not already received one today." Checking these conditions before claiming and then claiming in a separate step creates a TOCTOU (time-of-check / time-of-use) race: the condition may change between the check and the claim.

**How it works:**

The canonical solution is to push eligibility into the claim statement itself, using a `WHERE` predicate that both checks eligibility and claims atomically in a single database transaction. Because `SELECT FOR UPDATE SKIP LOCKED` holds a row lock for the duration of the statement, no other transaction can modify the eligibility state between the read and the write.

```sql
WITH claimed AS (
  SELECT j.id
  FROM jobs j
  JOIN accounts a ON a.id = j.account_id
  WHERE j.status = 'pending'
    AND a.balance > 0          -- eligibility predicate
    AND j.scheduled_at <= now()
  ORDER BY j.priority ASC, j.id ASC
  LIMIT 1
  FOR UPDATE OF j SKIP LOCKED
)
UPDATE jobs
SET status = 'running', worker_id = $1, claimed_at = now()
FROM claimed
WHERE jobs.id = claimed.id
RETURNING *;
```

If the eligibility predicate cannot be expressed as a SQL join (e.g., it requires calling an external API), the pattern breaks down. In that case, the correct approach is:

1. Claim the job atomically (without eligibility check).
2. Check eligibility inside the worker after claiming.
3. If ineligible, release the job back to the queue (update status to `pending` and clear `worker_id`) or to a `deferred` state.

This "claim then check" approach eliminates the race because only one worker can hold the claim. The trade-off is a wasted claim cycle.

**Real-world usage:**

- **Solid Queue:** Implements a `concurrency_maintenance` dispatcher that enforces per-job concurrency limits using a database semaphore. The semaphore check and the job dispatch are done in a way that prevents two workers from concurrently executing more than `N` instances of a given job type.
- **Oban:** Supports a `unique` option that checks for duplicate jobs at enqueue time using a database-level unique index, preventing duplicate eligibility from arising in the first place.
- **Sidekiq Enterprise (Unique Jobs):** Records a uniqueness key in Redis before pushing. Two jobs with the same `(class, args, queue)` tuple cannot coexist. Uses a Redis lock as the check-and-set primitive to avoid the TOCTOU window on enqueue.

**Eligibility conditions that can be pushed to SQL:**

- Job is not already claimed by another worker (`status = 'pending'`)
- Scheduled time has arrived (`scheduled_at <= now()`)
- Account has not hit a concurrency limit (join to a semaphore/counter table)
- Job is not blocked by a dependency (join to a `job_dependencies` table)
- Queue depth is below a cap (subquery count)

**Eligibility conditions that cannot easily be pushed to SQL:**

- External API rate limit check
- ML model prediction as a gate
- Real-time inventory reservation

For these, the claim-then-check approach is unavoidable, and idempotent release (BP-4) becomes important.

**Trade-offs:**

- *Complex SQL:* Large eligibility joins can increase query latency and make `SKIP LOCKED` less effective if many rows are filtered out after locking (each lock acquisition still has cost even if the row is ultimately skipped).
- *Index design is critical:* Partial indexes covering the most selective eligibility predicates (e.g., `status = 'pending' AND scheduled_at <= now()`) keep scans fast.
- *TOCTOU is fully eliminated only for database-backed conditions.* External state remains vulnerable to the race.

**When to use:** Always prefer atomic eligibility when conditions can be expressed in SQL. Use claim-then-check only when they cannot.

**When not to use:** Do not check eligibility in application code *before* issuing the claim statement. That pattern is never safe under concurrent workers.

---

## BP-6: Per-Queue Concurrency Caps with Database Semaphores

**Problem it solves:** Without concurrency limits, a surge of available jobs can exhaust downstream resources (database connection pools, external API rate limits, file descriptors) as every available worker claims and starts executing simultaneously. The system needs a way to say "at most N jobs of type X are executing at any moment" and enforce this under concurrent workers without a centralized lock server.

**How it works:**

A counting semaphore pattern implemented in the database:

1. At enqueue time, a "concurrency key" is derived from the job (e.g., `("send_email", user_id)`).
2. Before executing, a worker atomically increments a counter for that key and checks if the result exceeds the limit. If it does, the job is placed in a `blocked` state rather than `running`.
3. After job completion, the counter is decremented and any blocked jobs for that key are promoted back to `pending` so they can be claimed.

The atomic increment + check must itself use a database lock or compare-and-swap to prevent races.

**Real-world implementations:**

- **Solid Queue:** Uses a `solid_queue_semaphores` table with `value` and `limit` columns. The `concurrency_maintenance_interval` (default 600 seconds) runs a dispatcher that promotes blocked jobs when semaphore slots open. Jobs that exceed concurrency limits at claim time are placed in `solid_queue_blocked_executions`.
- **Oban Pro:** Provides a `global_limit` option per worker type that enforces cross-node concurrency limits using Postgres advisory locks combined with a count query.
- **Sidekiq Enterprise (Rate Limiting):** Exposes a `Sidekiq::Limiter` API for declaring limits based on concurrency or throughput (e.g., "5 concurrent executions" or "10 per second"). Limits are enforced via Redis atomic operations (Lua scripts using `EVAL`).
- **BullMQ:** Uses Redis sorted sets and atomic Lua scripts to implement rate limiters at the queue level. A queue can be configured with `limiter: { max: 10, duration: 1000 }` meaning at most 10 jobs per 1000ms window.
- **AWS SQS + Lambda concurrency:** Lambda's reserved concurrency setting provides a hard cap on parallel invocations from an SQS event source. When the cap is hit, unprocessed messages remain in the queue (with visibility timeout intact) until a Lambda slot frees up — this is backpressure from the platform layer.

**Backpressure patterns:**

Beyond per-queue caps, production systems use several backpressure strategies:

- **Reject at enqueue:** When the queue depth exceeds a threshold, new enqueue calls fail fast rather than adding to an unprocessable backlog. AWS documents this as critical for avoiding "insurmountable queue backlogs" — once a queue is unboundedly deep, it enters a degraded mode where recovery requires sustained double-capacity processing.
- **Metric-gated polling:** Workers check a "health" signal (e.g., connection pool saturation, downstream error rate) before polling for new jobs. If the signal is unhealthy, the worker pauses polling and sleeps. This is an application-level backpressure valve.
- **Weighted queue selection:** Sidekiq uses a weighted queue array where higher-priority queues appear more often in the random selection. This is not a hard cap but shifts capacity toward high-priority work under load.

**Trade-offs:**

- *Database semaphore overhead:* Each job start and completion requires a read-modify-write on the semaphore table. At high throughput, this can become a hot row. Some systems partition semaphores or use Redis for lower-latency counting.
- *Blocked job promotion latency:* If the maintenance sweep that unblocks jobs runs on a long interval (Solid Queue defaults to 600 seconds), a job can sit blocked for up to 10 minutes even after a slot opens. Tuning this interval to match workload expectations is important.
- *Cross-node limits require coordination:* In-process semaphores (e.g., a Go channel with a buffer of N) enforce concurrency within a single process. Cross-node limits require either the database or a Redis-backed counter.

**When to use:** Any workload with downstream rate limits, connection pool constraints, or business rules limiting concurrent execution (e.g., only one billing job per account at a time).

**When not to use:** High-throughput queues with lightweight, stateless jobs where the semaphore overhead exceeds the benefit; workloads where the external rate limit is enforced by the external service and retries are cheap.

---

## BP-7: Ephemeral Worker Identity with Process-Scoped Registration

**Problem it solves:** A coordinator needs to know which workers are alive, what they are running, and how to send them control signals (quiet, terminate). Worker identity must be recoverable from crashes without leaving stale registrations that pollute membership lists.

**How it works:**

Workers register at startup by writing a record that includes machine metadata and a unique process identifier. The registration record is *ephemeral* — it carries a TTL and is refreshed by the heartbeat. If the process dies without deregistering, the TTL expiry handles cleanup automatically. There is no persistent "worker account" — identity is scoped to a single process lifetime.

**Concrete registration payloads found in real systems:**

- **Sidekiq:** Each process writes to a Redis hash at key `processes:hostname:pid:uuid` containing: `hostname`, `pid`, `concurrency`, `busy` (count of active jobs), `beat` (Unix timestamp of last heartbeat), `rtt_us` (round-trip time to Redis in microseconds), `rss` (memory in KB). The key is added to a `processes` Redis set for membership testing. The hash TTL is 60 seconds.
- **Faktory:** Workers send a `HELLO` message at connection time containing: `wid` (unique worker ID), `hostname`, `pid`, `labels` (application-defined string array), and protocol `v` (version). This is stored server-side and associated with the TCP connection lifetime. Subsequent `BEAT` messages carry `rss_kb`.
- **Celery:** Workers register by emitting a `worker-online` event to the broker's event channel. Other processes (monitoring tools, other workers performing "mingling") consume these events. Worker state is tracked in-memory by monitoring consumers; it is not persisted to a durable store by default.
- **Oban:** Tracks workers via the `oban_beats` table using a `node` + `queue` composite identity. The `attempted_by` column on jobs records which node/queue executed them, enabling orphan detection after restarts.

**Ephemeral vs. persistent identity — the key distinction:**

| Property | Ephemeral (Sidekiq, Faktory) | Persistent (human-assigned) |
|---|---|---|
| Survives process restart | No — new PID = new identity | Yes — same ID across restarts |
| Cleanup on death | Automatic (TTL) | Requires explicit deregistration or reaper |
| Supports job resumption | No — in-flight jobs are requeued or lost | Yes — new process can resume previous work |
| Complexity | Low | High |

Most production job queue systems use ephemeral identity. Job resumption (continuing a job where it left off after a worker restart) is rarely implemented; the far more common pattern is *restart* semantics (requeue and re-execute from the beginning), which requires idempotent handlers (BP-4).

**Control plane signals:**

Both Sidekiq and Faktory support coordinator-initiated shutdown: the heartbeat response from the server carries a `state` field (`quiet` or `terminate`). A worker that receives `quiet` stops polling for new jobs; one that receives `terminate` begins graceful shutdown. This allows rolling deployments without manually SSHing into each worker machine.

**Trade-offs:**

- *Ephemeral identity loses in-flight job state on crash.* A worker that dies mid-job with ephemeral identity cannot resume; it must requeue. This is acceptable with idempotent handlers but problematic for long-running, expensive jobs.
- *Redis TTL-based cleanup has a race:* If Redis itself becomes unavailable, heartbeats cannot be written, causing the coordinator to believe all workers are dead even though they are running. This is a false-positive failure mode.
- *PID-based identity breaks in containerized environments:* In Docker/Kubernetes, PID 1 is typically the entrypoint process. PID reuse across container restarts can collide with unexpired Redis keys. Including a UUID or container instance ID in the registration key avoids this.

**When to use:** Ephemeral process-scoped identity is the correct default for stateless workers processing short-to-medium jobs (seconds to minutes).

**When not to use:** Long-running jobs (hours) where checkpointing and resumption are economically important; systems where the coordinator must maintain persistent state per worker across restarts.

---

## BP-8: Structured Observability for Worker Layer Diagnostics

**Problem it solves:** "Why isn't this job being picked up?" is one of the most common debugging questions in distributed job queue systems and one of the hardest to answer without the right instrumentation. Standard infrastructure metrics (CPU, memory, queue depth) often don't explain claim failures, eligibility mismatches, or stalled workers.

**How it works:**

Effective worker observability requires three signal types: structured logs with job-lifecycle events, queue-level metrics, and distributed traces that span job phases.

**Key events to emit as structured log records:**

Every log record for a job event should carry a minimum context:

```json
{
  "ts": "2026-03-20T14:23:01.123Z",
  "level": "info",
  "event": "job.claimed",
  "job_id": "01HX...",
  "job_kind": "send_invoice",
  "queue": "billing",
  "worker_id": "worker-hostname-12345",
  "attempt": 1,
  "wait_ms": 243,
  "priority": 2
}
```

**Lifecycle events that must be emitted:**

| Event | Fields beyond base |
|---|---|
| `job.enqueued` | `scheduled_at`, `priority`, `unique_key` |
| `job.claimed` | `wait_ms` (time in queue), `attempt` |
| `job.started` | — |
| `job.succeeded` | `duration_ms` |
| `job.failed` | `error_type`, `error_message`, `retry_at`, `attempt` |
| `job.abandoned` | `reason` (max attempts, stalled, etc.) |
| `job.stalled` | `last_heartbeat_ms_ago`, `worker_id` |
| `worker.started` | `concurrency`, `queues`, `hostname`, `pid` |
| `worker.heartbeat` | `active_jobs`, `rss_kb` |
| `worker.stopped` | `graceful`, `in_flight_requeued` |

**Diagnosing "why isn't this job being picked up":**

The question almost always has one of these root causes, each diagnosed differently:

1. *No workers are polling this queue.* Emit `worker.started` with the list of queues. Cross-reference with the queue name on the stuck job.
2. *Workers are polling but all slots are full (concurrency cap hit).* Emit `semaphore.full` events when a claim is blocked by a concurrency limit. Track a gauge `queue.blocked_jobs_count` per queue.
3. *The job's eligibility predicate never matches.* Log the eligibility check result at claim-scan time. Oban emits `job.discarded` with a reason; pg-boss surfaces this as job state transitions.
4. *The job's `scheduled_at` is in the future.* Log `job.scheduled_pending` with `scheduled_at` and `now()` so operators can verify the job is not simply deferred.
5. *All workers are dead.* The `worker.heartbeat` stream goes silent; alert on absence of heartbeats for more than `staleness_threshold` seconds.
6. *The job is in a stalled/stuck executing state.* Query for jobs in `executing` status with `claimed_at` older than `staleness_threshold`. This is what Oban's Lifeline plugin and pg-boss's reaper do automatically; exposing the count as a metric allows alerting before the reaper fires.

**Queue-level metrics (emit as counters/gauges):**

- `queue.depth` — pending job count per queue
- `queue.age_of_oldest_job_seconds` — the most important leading indicator; spikes before depth spikes
- `queue.in_flight_count` — jobs currently executing
- `queue.failed_count` — jobs in failed state awaiting retry
- `queue.stalled_count` — jobs executing beyond heartbeat threshold
- `worker.active_count` — workers with recent heartbeats
- `job.duration_seconds` (histogram by kind) — detect regressions in processing time

**AWS Builders' Library pattern — "AgeOfFirstAttempt":**

Amazon's internal queuing systems track the time from message arrival to the first processing attempt. This metric separates two failure modes that raw queue depth conflates: (1) jobs waiting for a normal amount of time because workers are busy, and (2) jobs that have been waiting far longer than expected because something is broken in the worker layer. Alarming on `age_of_first_attempt` at a percentile threshold (e.g., P99 > 5 minutes) provides earlier warning than alarming on queue depth.

**Distributed traces for multi-phase jobs:**

Jobs that span multiple phases (validate → fetch external data → process → persist → notify) benefit from trace spans per phase. The trace allows identifying which phase is slow without having to reproduce the issue. Inject a trace context into the job payload at enqueue time so the trace follows the job from producer to consumer.

**Trade-offs:**

- *Log volume:* Emitting structured events at every lifecycle transition generates significant log volume under high throughput. Use sampling for high-volume success events; always log failures and stalls at full fidelity.
- *Cardinality:* `job.id` is high-cardinality and should not be used as a metric label (it breaks time-series databases). Use `job.kind` and `queue` as metric labels; reserve `job.id` for log fields and trace IDs.
- *Correlation IDs must flow through:* The job record should carry a `correlation_id` from the HTTP request or upstream event that caused the enqueue. This allows tracing a job failure back to the user action that triggered it.

**When to use:** Always. The question is not whether to instrument but which signals to prioritize first. Start with queue depth and age-of-oldest-job metrics, then add job lifecycle event logging, then traces for multi-phase jobs.

---

## Summary Reference Table

| Pattern | Primary mechanism | Delivery guarantee | Recovery model | Real-world examples |
|---|---|---|---|---|
| Skip-Locked Claiming | `SELECT FOR UPDATE SKIP LOCKED` | Exactly-once claim | N/A (claim-level) | Oban, Solid Queue, River, pg-boss, Que |
| Visibility Timeout / Lease | Broker-managed invisibility timer | At-least-once | Timeout expiry → redeliver | SQS, Azure Service Bus, Faktory |
| Heartbeat with TTL | Periodic write + TTL + reaper scan | N/A (detection) | Reaper reclaims abandoned jobs | Sidekiq, Oban, Solid Queue, pg-boss, BullMQ |
| Idempotent Handlers | Unique constraints, dedup tables, idempotency keys | Exactly-once effects | Re-execute safely | All systems with at-least-once delivery |
| Atomic Eligibility | SQL predicate inside claim CTE | N/A (eligibility) | Prevents invalid claims | Solid Queue, Oban Pro |
| DB Semaphore / Concurrency Cap | Counter table or Redis atomic ops | N/A (throttling) | Blocks then unblocks | Solid Queue, Oban Pro, Sidekiq Enterprise, BullMQ |
| Ephemeral Worker Identity | TTL-keyed registration + heartbeat | N/A (identity) | Process death → TTL cleanup | Sidekiq, Faktory, Oban, Celery |
| Structured Observability | Lifecycle events + metrics + traces | N/A (visibility) | Enables rapid diagnosis | Best practice, not system-specific |
