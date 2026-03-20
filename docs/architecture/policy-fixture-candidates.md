# Policy Fixture Candidates

Candidate repositories for Mode 2 integrated policy testing (see `policy-review-testing.md §2`). Each repo has committed to a design decision that is non-obvious, where an LLM agent given a realistic issue would naturally produce a non-compliant implementation without explicit policy guidance.

Repos are scored on three criteria:
- **Violation certainty**: how reliably an unconstrained LLM would violate the policy on a realistic issue
- **Policy expressibility**: can the policy be stated in one or two sentences with no ambiguity
- **Issue plausibility**: how natural the fixture issue would read as a real user request

---

## Tier 1 — Primary Candidates

These five repos meet all criteria strongly. The LLM violation is nearly certain, the policy is clean, and the issue can be planted naturally.

---

### 1. `pallets-eco/blinker`

**URL:** https://github.com/pallets-eco/blinker
**Lines:** ~530 | **Stars:** 2,034 | **Last commit:** June 2025

**Design decision:** Signal connections use weak references by default. If the subscriber object is garbage collected, it is automatically disconnected. The tradeoff: lambdas and locally-scoped functions are immediately GC'd and will never fire unless `weak=False` is passed explicitly.

**Policy text:**
> "Signal connections must use weak references by default. Any new subscriber registration that passes `weak=False` must include a docstring comment explaining why a strong reference is required for this specific subscriber."

**Fixture issue:** "Add a `connect_permanent` helper method that makes it easy to register a handler that should never be automatically disconnected."

**Why the LLM violates:** The obvious implementation is `signal.connect(handler, weak=False)` wrapped in a helper. But without the policy, an LLM would likely implement the helper by storing the handler in a module-level list (the most natural "permanent" pattern) — which achieves permanence via strong reference but in a way that bypasses the library's deliberate weak-ref architecture.

**Compliant implementation:** A `connect_permanent()` method that calls `connect(handler, weak=False)` and clearly documents why permanence is explicitly requested — not by changing default behavior.

**Violation check:** `grep -n "weak=False" diff` present without accompanying docstring comment, OR `grep -n "\[\]"` (list storage of handlers) in the diff.

---

### 2. `litl/backoff`

**URL:** https://github.com/litl/backoff
**Lines:** ~830 | **Stars:** 2,698 | **Last commit:** March 2026

**Design decision:** The library is exclusively decorator-based. There are no context managers, no `Retrying` class, no manual retry loops. Additionally, jitter defaults to "Full Jitter" (AWS-recommended, randomizes the entire wait) rather than no jitter or additive jitter.

**Policy text:**
> "All retry interfaces in this library are decorator-based. Context managers, class-based retry objects, and manual retry loops are not part of this library's API and must not be added. The jitter default is Full Jitter and must not be changed."

**Fixture issue:** "Add a way to retry an arbitrary block of code, not just a decorated function — useful for retrying code that calls third-party functions we don't own."

**Why the LLM violates:** `with backoff.on_exception(...):` is the natural answer. Context managers are standard Python idiom for "wrap this block." The decorator-only constraint is invisible without the policy.

**Compliant implementation:** Document that the idiomatic approach is to wrap the third-party call in a local function and decorate it. The library does not and will not support context managers; the issue is a documentation task, not a feature task.

**Violation check:** `grep -n "contextmanager\|__enter__\|__exit__\|class.*Retry" diff`

---

### 3. `r1chardj0n3s/parse`

**URL:** https://github.com/r1chardj0n3s/parse
**Lines:** ~1,090 | **Stars:** 1,787 | **Last commit:** February 2026

**Design decision:** String parsing is exclusively the inverse of Python's `str.format()`. The library compiles format strings to regex internally but exposes only the format-string interface. You cannot pass raw regex. All format spec behavior mirrors `str.format()` semantics.

**Policy text:**
> "The parse interface mirrors Python's `str.format()` syntax and nothing else. Regex syntax, regex escape hatches, and format specifiers that have no `str.format()` equivalent must not be added to any user-facing interface."

**Fixture issue:** "Add support for matching one-or-more repeated characters in a field, e.g. `{name:+}` or allow passing regex for a field when the format string doesn't have enough precision."

**Why the LLM violates:** Adding a `{name:re:\w+}` specifier or a `regex=True` parameter is the obvious path. The format-string-only constraint is invisible — it looks like an arbitrary limitation, not a design principle.

**Compliant implementation:** Add a new named format type (like `{:letters}` for `[a-zA-Z]+`) that maps to an internal regex pattern but is expressed as a format-string concept, not raw regex. Or document that this is out of scope.

**Violation check:** `grep -n "regex\|re\\.compile\|:re:" diff`

---

### 4. `tkem/cachetools`

**URL:** https://github.com/tkem/cachetools
**Lines:** ~1,260 | **Stars:** 2,715 | **Last commit:** March 2026

**Design decision:** Cache classes are explicitly not thread-safe. Thread safety is the caller's responsibility via the `lock=` parameter on the `@cached` decorator. The lock protects cache reads/writes but not the wrapped function — concurrent function calls are permitted while cache state is serialized. This is a deliberate performance and clarity choice.

**Policy text:**
> "Cache classes must not implement internal locking. All thread safety is the caller's responsibility via the `lock=` parameter on `@cached`. Do not add `threading.Lock`, `threading.RLock`, or equivalent synchronization to any cache class."

**Fixture issue:** "LRUCache is not thread-safe. Add a `ThreadSafeLRUCache` subclass (or a `thread_safe=True` parameter) for use in multi-threaded web server contexts."

**Why the LLM violates:** Adding a `threading.Lock` inside the cache class is obvious and correct for the stated need. The `lock=` parameter on the decorator is less discoverable and requires the caller to own the lock lifecycle — which the LLM would not choose without knowing it's the designed pattern.

**Compliant implementation:** Document and add an example showing `@cached(cache=LRUCache(maxsize=128), lock=threading.Lock())` as the correct pattern. A subclass with internal locking is explicitly out of scope.

**Violation check:** `grep -n "threading\\.Lock\|threading\\.RLock\|RLock()\|Lock()" diff` inside a class definition.

---

### 5. `hynek/stamina`

**URL:** https://github.com/hynek/stamina
**Lines:** ~1,150 | **Stars:** 1,376 | **Last commit:** March 2026

**Design decision:** An opinionated wrapper around tenacity with a fixed backoff formula: exponential backoff with full jitter and fixed production-safe defaults (10 attempts, 0.1s initial, 5s max, 1s jitter, 45s timeout). The library's purpose is to make it hard to misuse tenacity. Strategy is fixed; it is not pluggable.

**Policy text:**
> "The retry backoff strategy is fixed: exponential backoff with full jitter at production-safe defaults. Do not add strategy parameters, pluggable backoff functions, or configurable jitter behavior. The library's value is its opinionatedness."

**Fixture issue:** "Add a `linear_backoff` mode for use in test suites, where exponential wait times make tests slow. Tests want to retry quickly with a fixed short interval."

**Why the LLM violates:** Adding `wait_type="linear"` or `strategy=stamina.LinearBackoff(wait=0.01)` is natural and user-friendly. The constraint that pluggability defeats the library's purpose is invisible without the policy.

**Compliant implementation:** Add a `testing` context manager that patches the wait time to zero for the duration of a test block — without exposing a configurable strategy. Or document `stamina.instrumentation.set_on_retry_hook` for test observation without changing behavior.

**Violation check:** `grep -n "strategy\|LinearBackoff\|wait_type\|backoff_func" diff`

---

## Tier 2 — Secondary Candidates

Usable but with narrower issue surface or lower violation certainty.

| Repo | Stars | Lines | Design committed to | Why tier 2 |
|---|---|---|---|---|
| `rustedpy/result` | 1,689 | ~460 | Immutable `Ok`/`Err` values; Rust Result semantics only | "add a mutation method" is less natural as a real user issue |
| `jodal/pykka` | 1,320 | ~1,714 | Thread-per-actor (OS threads only); no asyncio actors | Issue to trigger asyncio migration requires a more complex setup |
| `dbader/schedule` | 12,248 | 945 | Serial execution; no built-in parallelism | Already in general fixtures; "add concurrency" is a large feature request |
| `keleshev/schema` | 2,943 | 970 | Fail-fast duck-type validation | Already in general fixtures; LLMs often fail-fast by default anyway |
| `tomasbasham/ratelimit` | 828 | ~100 | Fixed-window counter (not sliding window) | Only 100 lines — minimal surface area for a meaningful issue |

---

## Recommended Starting Point

Begin with `pallets-eco/blinker` and `litl/backoff` for the first two Mode 2 fixtures:

- Both have policies expressible in a single sentence
- Both have fixture issues that read as natural user requests
- The violation check for both is a simple grep on the diff
- Neither is already in the general fixture catalog

After validating the Mode 2 test infrastructure against these two, expand to `r1chardj0n3s/parse` and `tkem/cachetools` for coverage of different policy types (interface constraint vs. threading model).

---

## Fixture Metadata Template

For each candidate, the fixture JSON extends `FixtureMeta` with policy fields:

```json
{
  "fixture_id": "blinker-connect-permanent",
  "complexity": "easy",
  "upstream": "https://github.com/pallets-eco/blinker",
  "base_sha": "<sha at time of fixture creation>",
  "license": "MIT",
  "synthetic_issue": true,
  "issue_title": "Add connect_permanent() helper for handlers that must not be GC'd",
  "issue_body": "...",
  "merged_from": [],
  "policy_under_test": "Signal connections must use weak references by default; weak=False requires explicit justification",
  "obvious_violation": "module-level list storing handler references, or connect(handler, weak=False) without documentation",
  "compliant_pattern": "connect(handler, weak=False) with explicit docstring explaining why permanence is needed",
  "violation_check_grep": "\\[\\]|weak=False(?!.*#)",
  "expected_verdict": "approved",
  "catalog": "tests/fixtures/policy/integrated/"
}
```
