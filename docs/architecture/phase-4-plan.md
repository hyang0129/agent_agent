# Phase 4 Implementation Plan — Agent Composites (Claude Code SDK)

*Implements: P01, P03, P05, P07, P08, P10, P11*
*Depends on: Phases 1-3 complete (models, state, DAG engine, executor, worktree, CLI, orchestrator)*

---

## Overview

Phase 4 replaces stub agents with real Claude Code SDK invocations. It builds the `src/agent_agent/agents/` directory, wires composite execution into the existing executor, and implements child DAG recursion. Each sub-phase (4a-4e) builds on the previous and is independently testable.

**SDK package:** `claude-agent-sdk` (PyPI), import as `claude_agent_sdk`
**Key SDK API:** `query(prompt, options)` returns `AsyncIterator[Message]`; collect `ResultMessage` at end for `total_cost_usd`.
**Config class:** `ClaudeAgentOptions` with fields: `system_prompt`, `allowed_tools`, `disallowed_tools`, `model`, `max_budget_usd`, `max_turns`, `cwd`, `permission_mode`, `can_use_tool`, `thinking`, `effort`, `output_format`.

> **SDK API VERIFICATION — Completed 2026-03-17**
>
> The prerequisite verification is complete. The SDK package name, imports, and core API (`query()` → `AsyncIterator[Message]` → `ResultMessage`) are confirmed. Several fields and behaviors diverge from the original plan assumptions. All divergences are documented in the [SDK Verification Addendum](#sdk-verification-addendum) at the end of this document, with corrections applied inline throughout the plan.
>
> **Summary of corrections applied:**
> 1. `permission_mode` — no `"never_ask"` in Python; use `"default"` + `can_use_tool` as sole gatekeeper
> 2. `can_use_tool` — async, 3 params `(tool_name, input_data, context)`, returns `PermissionResultAllow`/`PermissionResultDeny`
> 3. `thinking` + `effort` — separate fields; `thinking: {"type": "enabled", "budget_tokens": int}`, `effort: "high"`
> 4. `output_format` — `{"type": "json_schema", "schema": ...}` (not `"json"`)
> 5. Structured output — in `ResultMessage.structured_output`, not `.content`
> 6. Error handling — budget exceeded is `ResultMessage(subtype="error_max_budget_usd", is_error=True)`, not an exception; rate limit/auth are `ProcessError` with exit codes
> 7. Denial counter threshold — 5 (not 3); `interrupt=True` on 5th denial stops session
> 8. Streaming mode required — `can_use_tool` requires prompt as `AsyncIterable`

---

## Phase 4a — SDK Wrapper + Base Agent

### Files to Create

#### `src/agent_agent/agents/__init__.py`

```python
"""Agent composites — real Claude Code SDK invocations replacing Phase 2/3 stubs."""
```

Empty module init. No public re-exports.

#### `src/agent_agent/agents/base.py`

**Purpose:** Single entry point for all SDK invocations. Every sub-agent (Programmer, Debugger, Reviewer, etc.) calls `invoke_agent()` with its specific configuration.

```python
"""SDK wrapper — invoke_agent() for all sub-agent types.

Wraps the Claude Code Agent SDK. Enforces iteration caps, argument validation,
structured output parsing, cost tracking, and tool call logging [P8.6].
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

from pydantic import BaseModel

from ..models.agent import AgentOutput
from ..models.context import NodeContext
from ..observability import EventType, emit_event


@dataclass(frozen=True)
class ToolPermission:
    """Maps a capability intent to SDK tool names and argument validation rules."""
    intent: str                           # e.g. "read_files", "write_files", "execute_tests"
    sdk_tool_names: list[str]             # e.g. ["Read", "Glob", "Grep"]
    validate_args: Callable[[str, dict[str, Any]], bool] | None = None
    # validate_args(tool_name, args) -> True if allowed, False if rejected


@dataclass(frozen=True)
class SubAgentConfig:
    """Complete configuration for a sub-agent SDK invocation."""
    name: str                             # e.g. "programmer", "debugger", "reviewer"
    system_prompt: str
    permissions: list[ToolPermission]
    output_model: type[BaseModel]         # Pydantic model for structured output
    max_turns: int                        # iteration cap per P10.3
    use_thinking: bool = False            # extended reasoning for Plan composite [P10.11]
    thinking_budget_tokens: int = 10000   # thinking budget when use_thinking=True
    effort: str = "high"                  # "low", "medium", "high", "max"


def compute_sdk_backstop(node_allocation_usd: float, total_budget_usd: float) -> float:
    """Compute SDK budget backstop for a single sub-agent invocation [HG-7].

    Formula: min(node_allocation * 2, node_allocation + 2.5% of total_budget).
    This prevents runaway spending while giving each invocation reasonable headroom.
    The backstop is a safety net — BudgetManager.record_usage() is the real enforcement.
    """
    return min(
        node_allocation_usd * 2,
        node_allocation_usd + total_budget_usd * 0.025,
    )


async def invoke_agent(
    config: SubAgentConfig,
    node_context: NodeContext,
    model: str,
    sdk_budget_backstop_usd: float,
    cwd: str,
    dag_run_id: str,
    node_id: str,
) -> tuple[AgentOutput, float]:
    """Invoke an SDK sub-agent and return (parsed_output, cost_usd).

    Returns AgentOutput (the discriminated union); callers narrow via
    isinstance checks. The concrete type depends on config.output_model
    (e.g., PlanOutput, CodeOutput, ReviewOutput).
    """
    ...
```

**`invoke_agent()` implementation steps:**

1. **Build `allowed_tools` list** from `config.permissions`: flatten all `sdk_tool_names` across all `ToolPermission` entries.

2. **Build `can_use_tool` callback** from `config.permissions`:

   The SDK `can_use_tool` is async with 3 parameters: `(tool_name, input_data, context)`.
   It returns `PermissionResultAllow()` or `PermissionResultDeny(message=..., interrupt=...)`.

   **Permission strategy:** `permission_mode="default"` with `allowed_tools=[]` and
   `disallowed_tools=[]`. Every tool call goes through this callback — it is the sole
   gatekeeper. The SDK auto-sets `--permission-prompt-tool stdio` when `can_use_tool` is
   provided, so the CLI never prompts interactively.

   **Denial strategy:** A single counter tracks all denials (unauthorized tool OR bad args).
   At 5 denials, `interrupt=True` stops the session. The node is classified as SafetyViolation.
   No human involvement in the coding node.

   ```python
   from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

   denial_count = 0  # track denials for SafetyViolation escalation
   DENIAL_THRESHOLD = 5

   async def can_use_tool(
       tool_name: str,
       input_data: dict[str, Any],
       context: Any,  # ToolPermissionContext — unused but required by SDK
   ) -> PermissionResultAllow | PermissionResultDeny:
       nonlocal denial_count
       for perm in config.permissions:
           if tool_name in perm.sdk_tool_names:
               if perm.validate_args is not None:
                   allowed = perm.validate_args(tool_name, input_data)
               else:
                   allowed = True
               emit_event(
                   EventType.TOOL_CALLED, dag_run_id,
                   node_id=node_id,
                   tool_name=tool_name,
                   allowed=allowed,
                   agent_type=config.name,
               )
               if allowed:
                   return PermissionResultAllow()
               # Authorized tool, bad arguments
               denial_count += 1
               reason = "argument_validation_failed"
               emit_event(
                   EventType.TOOL_DENIED, dag_run_id,
                   node_id=node_id,
                   tool_name=tool_name,
                   agent_type=config.name,
                   reason=reason,
                   denial_count=denial_count,
               )
               should_interrupt = denial_count >= DENIAL_THRESHOLD
               return PermissionResultDeny(
                   message=f"Denied: {reason} for {tool_name} (denial {denial_count}/{DENIAL_THRESHOLD})",
                   interrupt=should_interrupt,
               )
       # Tool not in any permission set → unauthorized
       denial_count += 1
       reason = "tool_not_permitted"
       emit_event(
           EventType.TOOL_DENIED, dag_run_id,
           node_id=node_id,
           tool_name=tool_name,
           agent_type=config.name,
           reason=reason,
           denial_count=denial_count,
       )
       should_interrupt = denial_count >= DENIAL_THRESHOLD
       return PermissionResultDeny(
           message=f"Denied: {tool_name} is not permitted for {config.name} (denial {denial_count}/{DENIAL_THRESHOLD})",
           interrupt=should_interrupt,
       )
   ```

   When `interrupt=True` is returned on the 5th denial, the SDK stops the session.
   `ResultMessage` will have `is_error=True`. The `invoke_agent()` error handling
   (step 6) checks the denial counter after the session ends and raises
   `SafetyViolationError` if `denial_count >= DENIAL_THRESHOLD`.

3. **Serialize NodeContext** into the user message (see [NodeContext Serialization Format](#nodecontext-serialization-format) below).

4. **Build `output_format`** from `config.output_model`:
   ```python
   output_format = {
       "type": "json_schema",
       "schema": config.output_model.model_json_schema(),
   }
   ```
   Note: the SDK expects `"json_schema"`, not `"json"`. If the SDK does not support
   `output_format`, fall back to system prompt enforcement and post-hoc parsing (see HG-8).

5. **Build `ClaudeAgentOptions`**:
   ```python
   from claude_agent_sdk import ClaudeAgentOptions, query

   thinking_config = None
   effort_value = None
   if config.use_thinking:
       thinking_config = {"type": "enabled", "budget_tokens": config.thinking_budget_tokens}
       effort_value = config.effort  # "high", "max", etc.

   options = ClaudeAgentOptions(
       system_prompt=config.system_prompt,
       allowed_tools=[],              # empty — can_use_tool is sole gatekeeper
       disallowed_tools=[],           # empty — can_use_tool handles all denials
       model=model,
       max_budget_usd=sdk_budget_backstop_usd,
       max_turns=config.max_turns,
       cwd=cwd,
       permission_mode="default",     # no "never_ask" in Python; callback handles everything
       can_use_tool=can_use_tool_callback,
       thinking=thinking_config,
       effort=effort_value,
       output_format=output_format,
   )
   ```

   **Streaming mode requirement:** The SDK requires `prompt` to be an `AsyncIterable`
   when `can_use_tool` is set (streaming mode). Wrap the user message:

   ```python
   async def _prompt_iter(msg: str):
       yield {"role": "user", "content": msg}

   prompt_source = _prompt_iter(user_message)
   ```

6. **Call `query()` and collect result**:
   ```python
   from claude_agent_sdk import ResultMessage

   result_message = None
   async for message in query(prompt=prompt_source, options=options):
       if isinstance(message, ResultMessage):
           result_message = message

   if result_message is None:
       raise AgentError("SDK returned no messages (empty iterator)")

   # Check if session was interrupted due to denial threshold
   if denial_count >= DENIAL_THRESHOLD:
       raise SafetyViolationError(
           f"Agent {config.name} hit {denial_count} tool denials "
           f"(threshold={DENIAL_THRESHOLD}); classified as Safety Violation"
       )

   # Check for SDK-reported errors
   if result_message.is_error:
       if result_message.subtype == "error_max_budget_usd":
           raise ResourceExhaustionError(
               f"Agent {config.name} exceeded SDK budget backstop: "
               f"${sdk_budget_backstop_usd:.2f}"
           )
       else:
           raise AgentError(
               f"SDK session ended with error: subtype={result_message.subtype}, "
               f"result={str(result_message.result)[:500]}"
           )

   cost_usd = result_message.total_cost_usd or 0.0
   ```

7. **Parse structured output** from `result_message` into `config.output_model`:
   ```python
   # Prefer structured_output (populated when output_format is used);
   # fall back to result (text) if structured_output is None
   raw_output = result_message.structured_output
   if raw_output is None:
       # Fallback: parse result text as JSON
       if result_message.result is None:
           raise AgentError(
               f"SDK returned no output: subtype={result_message.subtype}, "
               f"num_turns={result_message.num_turns}"
           )
       try:
           raw_output = json.loads(result_message.result)
       except json.JSONDecodeError as exc:
           raise AgentError(
               f"Failed to parse SDK output as JSON: {exc}. "
               f"Raw result (truncated): {str(result_message.result)[:500]}"
           ) from exc

   try:
       parsed_output = config.output_model.model_validate(raw_output)
   except ValidationError as exc:
       raise AgentError(
           f"SDK output failed Pydantic validation for {config.output_model.__name__}: {exc}"
       ) from exc
   ```
   JSON parse failure and Pydantic validation failure are both raised as `AgentError` (Agent Error per P10.7).

8. **Return `(parsed_output, cost_usd)`**.

**Error mapping — SDK conditions to P10.7 failure taxonomy:**

The SDK reports most error conditions via `ResultMessage` fields, not exceptions.
Only connection/process failures raise exceptions.

| SDK Condition | Detection | Maps To | Executor Exception |
|---------------|-----------|---------|-------------------|
| `ProcessError` with rate-limit exit code / network timeout | `except ProcessError` + inspect `.exit_code` / `.stderr` | Transient | `TransientError` |
| `ProcessError` with auth failure exit code | `except ProcessError` + inspect `.exit_code` / `.stderr` | Deterministic | `DeterministicError` |
| `CLIConnectionError` / `CLINotFoundError` | `except CLIConnectionError` | Deterministic | `DeterministicError` |
| `ResultMessage(subtype="error_max_budget_usd")` | `result_message.is_error and subtype == "error_max_budget_usd"` | Resource Exhaustion | `ResourceExhaustionError` |
| `max_turns` reached (session ends normally) | `result_message.result is None and result_message.structured_output is None` | Resource Exhaustion | `ResourceExhaustionError` |
| Pydantic validation failure on output | `except ValidationError` after `model_validate()` | Agent Error | `AgentError` |
| JSON parse failure on output | `except json.JSONDecodeError` | Agent Error | `AgentError` |
| `can_use_tool` denial count ≥ 5 | `denial_count >= DENIAL_THRESHOLD` after session | Safety Violation | `SafetyViolationError` |
| `CLIJSONDecodeError` | `except CLIJSONDecodeError` | Unknown | re-raise (executor classifies as `UNKNOWN`) |
| Unexpected exception | `except Exception` | Unknown | re-raise as-is |

The `invoke_agent()` function catches SDK-specific exceptions and re-raises as the appropriate executor exception type from `dag.executor`. Import the exception classes:
```python
from ..dag.executor import (
    TransientError, AgentError, ResourceExhaustionError,
    DeterministicError, SafetyViolationError,
)
from claude_agent_sdk import (
    ProcessError, CLIConnectionError, CLINotFoundError, CLIJSONDecodeError,
    PermissionResultAllow, PermissionResultDeny, ResultMessage,
)
```

### NodeContext Serialization Format

Every sub-agent receives `NodeContext` as a structured user message. The format is a single string built from sections:

```python
def serialize_node_context(ctx: NodeContext, role_hint: str) -> str:
    """Serialize NodeContext into the SDK user message.

    role_hint is a short string like 'programmer', 'reviewer' etc. used to
    label the context sections relevant to that role.
    """
    sections = []

    # 1. Issue (always verbatim, never omitted) [P5.3]
    sections.append(f"## GitHub Issue\n\nURL: {ctx.issue.url}\nTitle: {ctx.issue.title}\n\n{ctx.issue.body}")

    # 2. Repository context (always verbatim) [P5.3]
    sections.append(f"## Repository\n\nPath: {ctx.repo_metadata.path}\nDefault branch: {ctx.repo_metadata.default_branch}\nLanguage: {ctx.repo_metadata.language or 'unknown'}\nFramework: {ctx.repo_metadata.framework or 'unknown'}")

    # 3. CLAUDE.md (always verbatim)
    sections.append(f"## Target Repo CLAUDE.md\n\n{ctx.repo_metadata.claude_md}")

    # 4. Parent outputs (typed Pydantic objects serialized as JSON)
    if ctx.parent_outputs:
        parts = []
        for node_id, output in ctx.parent_outputs.items():
            parts.append(f"### Output from node {node_id}\n\n```json\n{output.model_dump_json(indent=2)}\n```")
        sections.append(f"## Upstream Outputs\n\n" + "\n\n".join(parts))

    # 5. Ancestor context (if any)
    # Note: AncestorEntry.summarized field exists in models/context.py
    # but is not documented in data-models.md. The source file is canonical.
    if ctx.ancestor_context.entries:
        parts = []
        for entry in ctx.ancestor_context.entries:
            label = "(summarized)" if entry.summarized else "(full)"
            content = entry.output if isinstance(entry.output, str) else entry.output.model_dump_json(indent=2)
            parts.append(f"### Ancestor {entry.node_id} (depth {entry.depth}) {label}\n\n{content}")
        sections.append(f"## Ancestor Context\n\n" + "\n\n".join(parts))

    # 6. SharedContextView (discoveries, summary, active plan)
    scv = ctx.shared_context_view
    if scv.summary or scv.active_plan or scv.file_mappings or scv.root_causes:
        sc_parts = []
        if scv.summary:
            sc_parts.append(f"Summary: {scv.summary}")
        if scv.active_plan:
            sc_parts.append(f"Active plan: {scv.active_plan}")
        for category, records in [
            ("File mappings", scv.file_mappings),
            ("Root causes", scv.root_causes),
            ("Constraints", scv.constraints),
            ("Design decisions", scv.design_decisions),
            ("Negative findings", scv.negative_findings),
        ]:
            if records:
                sc_parts.append(f"### {category}\n" + "\n".join(
                    f"- [{r.source_node_id}] {r.discovery.model_dump_json()}" for r in records
                ))
        sections.append(f"## Shared Context\n\n" + "\n\n".join(sc_parts))

    return "\n\n---\n\n".join(sections)
```

This function lives in `src/agent_agent/agents/base.py`.

### Tool Permission Definitions

Define in `src/agent_agent/agents/tools.py`:

```python
"""Tool permission definitions — maps P3.3 capability intents to SDK tool names.

Tool names are intent-level [P3.4/P8.5]. The SDK exposes: Read, Edit, Write,
Glob, Grep, Bash, WebFetch, WebSearch, NotebookEdit.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import ToolPermission


# ---------------------------------------------------------------------------
# Argument validators
# ---------------------------------------------------------------------------

def _validate_worktree_path(worktree_root: str):
    """Return a validator that rejects file operations outside worktree_root [P8.5/P10.13].

    Uses a whitelist approach [HG-5]: absolute paths are allowed only if they match
    known-safe prefixes (system paths, the worktree itself). Relative paths are allowed
    (they resolve within the cwd, which is the worktree). Phase 6 adds more sophisticated
    shell command parsing.
    """
    # Whitelist of safe absolute path prefixes — system paths that agents may reference
    SAFE_PREFIXES = (
        worktree_root,
        "/usr/",
        "/bin/",
        "/etc/",
        "/tmp/",
        "/dev/null",
        "/dev/stdout",
        "/dev/stderr",
        "/proc/",
        "/sys/",
    )

    def _is_safe_abs_path(path: str) -> bool:
        """Return True if an absolute path is in the whitelist."""
        return any(path.startswith(prefix) for prefix in SAFE_PREFIXES)

    def validator(tool_name: str, args: dict[str, Any]) -> bool:
        # Check path-bearing arguments
        for key in ("file_path", "path", "command"):
            value = args.get(key)
            if value is None:
                continue
            if key == "command":
                # For Bash: extract absolute paths and check against whitelist
                if isinstance(value, str):
                    import re
                    abs_paths = re.findall(r'/[\w/.-]+', value)
                    for p in abs_paths:
                        if not _is_safe_abs_path(p):
                            return False
                return True
            else:
                # For file_path/path arguments: relative paths are fine (resolve in cwd)
                # Absolute paths must be under worktree_root
                if isinstance(value, str) and value.startswith("/"):
                    resolved = str(Path(value).resolve())
                    if not resolved.startswith(worktree_root):
                        return False
        return True
    return validator


def _validate_no_git_push(tool_name: str, args: dict[str, Any]) -> bool:
    """Reject Bash commands that push to remote [P10.13 — push is composite-level]."""
    cmd = args.get("command", "")
    if isinstance(cmd, str) and "git push" in cmd:
        return False
    return True


def _validate_read_only_bash(tool_name: str, args: dict[str, Any]) -> bool:
    """Reject Bash commands that write files or mutate git."""
    cmd = args.get("command", "")
    if not isinstance(cmd, str):
        return False
    # Reject common write patterns
    write_patterns = [
        "git commit", "git add", "git push", "git checkout", "git reset",
        "git merge", "git rebase", "git stash",
        "> ", ">> ", "tee ", "mv ", "rm ", "cp ", "mkdir ", "touch ",
        "sed -i", "chmod ", "chown ",
    ]
    for pattern in write_patterns:
        if pattern in cmd:
            return False
    return True


# ---------------------------------------------------------------------------
# Per-sub-agent tool permissions
# ---------------------------------------------------------------------------

def plan_permissions() -> list[ToolPermission]:
    """ResearchPlannerOrchestrator: read-only [P3.3]."""
    return [
        ToolPermission(
            intent="read_files",
            sdk_tool_names=["Read", "Glob", "Grep"],
        ),
        ToolPermission(
            intent="execute_read_commands",
            sdk_tool_names=["Bash"],
            validate_args=_validate_read_only_bash,
        ),
    ]


def programmer_permissions(worktree_root: str) -> list[ToolPermission]:
    """Programmer: read + write files + git within worktree [P3.3/P10.13]."""
    wt_validator = _validate_worktree_path(worktree_root)

    def combined_validator(tool_name: str, args: dict[str, Any]) -> bool:
        if not wt_validator(tool_name, args):
            return False
        if not _validate_no_git_push(tool_name, args):
            return False
        return True

    return [
        ToolPermission(
            intent="read_files",
            sdk_tool_names=["Read", "Glob", "Grep"],
        ),
        ToolPermission(
            intent="write_files",
            sdk_tool_names=["Edit", "Write"],
            validate_args=wt_validator,
        ),
        ToolPermission(
            intent="execute_commands",
            sdk_tool_names=["Bash"],
            validate_args=combined_validator,
        ),
    ]


def test_designer_permissions() -> list[ToolPermission]:
    """Test Designer: read-only [P3.3]."""
    return [
        ToolPermission(
            intent="read_files",
            sdk_tool_names=["Read", "Glob", "Grep"],
        ),
        ToolPermission(
            intent="execute_read_commands",
            sdk_tool_names=["Bash"],
            validate_args=_validate_read_only_bash,
        ),
    ]


def test_executor_permissions(worktree_root: str) -> list[ToolPermission]:
    """Test Executor: read files + run test commands within worktree [P3.3].

    The Test Executor may write temporary/generated files during test execution
    (e.g., coverage reports, __pycache__, .pytest_cache). Write enforcement is
    NOT at the tool layer — instead, the CodingComposite validates post-execution
    that the net effect is zero changes to Programmer-committed source files via
    `git diff`. This avoids fragile Bash blocklists and matches real test behavior.

    Git operations are still blocked via _validate_no_git_push composed with
    _validate_worktree_path.
    """
    wt_validator = _validate_worktree_path(worktree_root)

    def _validate_test_executor_bash(tool_name: str, args: dict[str, Any]) -> bool:
        # Block paths outside worktree
        if not wt_validator(tool_name, args):
            return False
        # Block git push (but allow git status, git log, etc.)
        if not _validate_no_git_push(tool_name, args):
            return False
        # Block git commit/add/reset (test executor should not touch git index)
        cmd = args.get("command", "")
        git_mutation_patterns = ["git commit", "git add", "git reset", "git checkout", "git stash"]
        for pattern in git_mutation_patterns:
            if pattern in cmd:
                return False
        return True

    return [
        ToolPermission(
            intent="read_files",
            sdk_tool_names=["Read", "Glob", "Grep"],
        ),
        ToolPermission(
            intent="execute_tests",
            sdk_tool_names=["Bash"],
            validate_args=_validate_test_executor_bash,
        ),
    ]


def debugger_permissions(worktree_root: str) -> list[ToolPermission]:
    """Debugger: same as Programmer — read + write + git within worktree [P3.3]."""
    return programmer_permissions(worktree_root)


def reviewer_permissions(worktree_root: str) -> list[ToolPermission]:
    """Reviewer: read-only access to one branch's worktree [P3.3]."""
    return [
        ToolPermission(
            intent="read_files",
            sdk_tool_names=["Read", "Glob", "Grep"],
        ),
        ToolPermission(
            intent="execute_read_commands",
            sdk_tool_names=["Bash"],
            validate_args=_validate_read_only_bash,
        ),
    ]
```

### System Prompt Skeletons

Define in `src/agent_agent/agents/prompts.py`:

```python
"""System prompt templates for each sub-agent type.

Prompts define the role, constraints, and output format instructions.
They do NOT include full policy text — Phase 6 adds selective policy context [planning decision].
"""

RESEARCH_PLANNER_ORCHESTRATOR = """\
You are a ResearchPlannerOrchestrator agent. Your job is to analyze a GitHub issue \
and the target repository, then produce a structured plan for resolving the issue.

## Role
- Read and understand the GitHub issue
- Explore the repository structure, relevant source files, and git history
- Identify root causes, relevant files, and constraints
- Produce a ChildDAGSpec that decomposes the work into parallel or sequential \
  Coding composites (2-5 per level; 6-7 requires justification; 8+ is rejected)

## Constraints
- You have READ-ONLY access. You cannot modify files, run git commands that change \
  state, or create PRs.
- Your working directory is the primary checkout of the target repository.
- Every discovery you make should be included in the `discoveries` field of your output.

## Output Format
Return a JSON object matching the PlanOutput schema:
- `type`: always "plan"
- `investigation_summary`: A thorough summary of your investigation findings
- `child_dag`: A ChildDAGSpec object (or null if work is complete)
  - `composites`: list of CompositeSpec objects, each with `id`, `scope`, `branch_suffix`
  - `sequential_edges`: list of SequentialEdge objects (empty = all parallel)
  - `justification`: required if 6+ composites
- `discoveries`: list of Discovery objects you found during investigation
"""

CONSOLIDATION_PLANNER = """\
You are a ResearchPlannerOrchestrator agent in consolidation mode. You have received \
the results from all Coding and Review composites at this level.

## Role
- Evaluate all (CodeOutput, ReviewOutput) pairs from the completed level
- If all branches are approved: return child_dag=null (work complete)
- If any branch needs rework or was rejected: produce a child DAG spec for rework
- Consider downstream impacts flagged by Reviewers

## Constraints
- You have READ-ONLY access.
- Do not repeat work that was already approved.
- Only include rework composites for branches that need it.

## Output Format
Return a JSON object matching the PlanOutput schema.
- `child_dag`: null if all work is complete and approved; otherwise a ChildDAGSpec \
  for the rework level
"""

PROGRAMMER = """\
You are a Programmer agent working in an isolated git worktree.

## Role
- Implement the changes described in the upstream PlanOutput scope
- Write clean, well-structured code that resolves the assigned sub-task
- Stage and commit your changes with a descriptive commit message
- Do NOT push — the composite handles push-on-exit

## Constraints
- Work ONLY within your worktree directory: {worktree_path}
- Do not reference the primary checkout or other worktrees
- Do not create or comment on PRs
- Do not run `git push`

## Output Format
Return a JSON object matching the CodeOutput schema:
- `type`: always "code"
- `summary`: one-paragraph description of changes
- `files_changed`: list of relative paths within the worktree
- `branch_name`: the branch name (provided in your context)
- `commit_sha`: the commit SHA after your final commit (or null if no commit)
- `tests_passed`: null (you do not run tests)
- `discoveries`: any discoveries found during implementation
"""

TEST_DESIGNER = """\
You are a Test Designer agent. You design test plans for code changes.

## Role
- Review the Programmer's CodeOutput to understand what changed
- Design a test plan covering the changes: what to test, edge cases, assertions
- Do NOT write test files or run tests — only produce a plan

## Constraints
- You have READ-ONLY access
- Do not modify any files
- Focus on testable behaviors, not implementation details

## Output Format
Return a JSON object matching the AgentTestOutput schema:
- `type`: always "test"
- `role`: always "plan"
- `summary`: brief summary of the test strategy
- `test_plan`: detailed prose description of what to test and how
"""

TEST_EXECUTOR = """\
You are a Test Executor agent. You run the test suite and report results.

## Role
- Run the test suite in the worktree directory: {worktree_path}
- Report pass/fail status and failure details
- Do NOT fix failing tests — that is the Debugger's job

## Constraints
- Work within the worktree directory
- Do not modify source files — any source file changes will be detected and rejected
- Do not perform git operations (commit, add, push, checkout, etc.)
- Run tests using the project's configured test runner
- You may create temporary files if needed for test execution (they will be cleaned up)

## Output Format
Return a JSON object matching the AgentTestOutput schema:
- `type`: always "test"
- `role`: always "results"
- `summary`: brief summary of test results
- `passed`: true if all tests pass, false otherwise
- `total_tests`: number of tests run
- `failed_tests`: number of failures
- `failure_details`: raw test output (truncated to 2000 chars if needed)
"""

DEBUGGER = """\
You are a Debugger agent working in an isolated git worktree.

## Role
- Diagnose test failures from the Test Executor's output
- Write corrective changes to fix failing tests
- Stage and commit your fixes with a descriptive commit message
- Do NOT push — the composite handles push-on-exit

## Constraints
- Work ONLY within your worktree directory: {worktree_path}
- Do not reference the primary checkout or other worktrees
- Do not create or comment on PRs
- Do not run `git push`

## Output Format
Return a JSON object matching the CodeOutput schema:
- `type`: always "code"
- `summary`: description of the debugging changes
- `files_changed`: list of relative paths
- `branch_name`: the branch name
- `commit_sha`: commit SHA after your fix commit
- `tests_passed`: null (Test Executor will re-verify in next cycle)
- `discoveries`: any discoveries from debugging
"""

REVIEWER = """\
You are a Reviewer agent evaluating a code branch.

## Role
- Review the code changes on the branch checked out in your worktree: {worktree_path}
- Evaluate: code quality, correctness, test coverage, adherence to the issue requirements
- Produce a verdict: approved, needs_rework, or rejected
- Flag any downstream impacts that the consolidation planner should know about

## Constraints
- You have READ-ONLY access
- Do not modify files, run git mutations, or merge PRs
- Evaluate this branch in isolation — do not compare with sibling branches

## Output Format
Return a JSON object matching the ReviewOutput schema:
- `type`: always "review"
- `verdict`: "approved", "needs_rework", or "rejected"
- `summary`: overall assessment
- `findings`: list of ReviewFinding objects (severity, location, description, suggested_fix)
- `downstream_impacts`: list of cross-branch concerns for the Plan composite
- `discoveries`: any discoveries
"""
```

### Tests for Phase 4a

#### `tests/unit/test_agents_base.py`

```
Tests:
1. serialize_node_context() includes issue verbatim [P5.3]
2. serialize_node_context() includes repo_metadata verbatim [P5.3]
3. serialize_node_context() includes parent_outputs as JSON
4. serialize_node_context() includes shared_context_view discoveries
5. can_use_tool callback returns PermissionResultAllow for permitted tools
6. can_use_tool callback returns PermissionResultDeny for unpermitted tools and logs TOOL_DENIED [P8.6]
7. can_use_tool callback returns PermissionResultDeny for permitted tools with bad arguments [P8.5]
8. can_use_tool deny message includes tool name, reason, and denial count
9. _validate_worktree_path rejects paths outside worktree
10. _validate_worktree_path allows paths inside worktree
11. _validate_no_git_push rejects "git push" in commands
12. _validate_read_only_bash rejects write patterns
13. Each *_permissions() function returns correct tool names for P3.3
14. test_executor_permissions: blocks git mutation commands, allows test runner commands [P3.3]
15. ProcessError with rate-limit stderr -> TransientError mapping
16. ProcessError with auth-failure stderr -> DeterministicError mapping
17. CLIConnectionError -> DeterministicError mapping
18. ResultMessage(subtype="error_max_budget_usd") -> ResourceExhaustionError mapping
19. SDK empty result (no messages) -> AgentError mapping
20. SDK json.JSONDecodeError on result -> AgentError mapping
21. ResultMessage.structured_output used when present, falls back to .result
22. can_use_tool denial counter: 5 denials -> interrupt=True + SafetyViolationError
```

All unit tests are pure Python with mocked SDK calls. No real SDK invocations.

#### `tests/component/test_sdk_wrapper.py`

```
@pytest.mark.sdk  — real SDK calls; $1 hard budget cap

Tests:
1. invoke_agent with a trivial prompt returns a valid Pydantic-parsed output
2. invoke_agent respects max_turns (set max_turns=1, verify ResourceExhaustionError)
3. invoke_agent returns cost_usd > 0
4. invoke_agent with invalid output_format raises AgentError

Fixture: No git needed. Uses a minimal SubAgentConfig with PlanOutput model.
Budget: sdk_budget_backstop_usd = 1.0 per test
```

### Dependencies to Add

Add to `pyproject.toml`:
```
"claude-agent-sdk>=0.1.49"
```

### Gate

Unit tests in `tests/unit/test_agents_base.py` pass. Component tests in `tests/component/test_sdk_wrapper.py` pass with a valid `ANTHROPIC_API_KEY` set.

---

## Phase 4b — Plan Composite

### Files to Create

#### `src/agent_agent/agents/plan.py`

```python
"""Plan composite — ResearchPlannerOrchestrator sub-agent.

Two invocation modes:
  L0 (analysis): reads issue + repo, produces ChildDAGSpec
  Consolidation: receives all (CodeOutput, ReviewOutput) pairs, decides next steps

Uses extended reasoning (thinking enabled) [P10.11].
Read-only tools only [P3.3].
cwd = primary checkout (--repo path) — no worktree needed for Plan composite.
"""
from __future__ import annotations

from ..budget import BudgetManager
from ..config import Settings
from ..dag.executor import AgentError
from ..models.agent import PlanOutput
from ..models.context import NodeContext
from .base import SubAgentConfig, compute_sdk_backstop, invoke_agent
from .prompts import RESEARCH_PLANNER_ORCHESTRATOR, CONSOLIDATION_PLANNER
from .tools import plan_permissions


class PlanComposite:
    """Executes a Plan composite node (single ResearchPlannerOrchestrator invocation)."""

    def __init__(self, settings: Settings, repo_path: str, budget: BudgetManager) -> None:
        self._settings = settings
        self._repo_path = repo_path
        self._budget = budget

    async def execute(
        self,
        node_context: NodeContext,
        dag_run_id: str,
        node_id: str,
        is_consolidation: bool,
    ) -> tuple[PlanOutput, float]:
        """Invoke ResearchPlannerOrchestrator and return (PlanOutput, cost_usd).

        Args:
            is_consolidation: True for terminal Plan composites (post-review);
                             False for L0 analysis.
        """
        system_prompt = CONSOLIDATION_PLANNER if is_consolidation else RESEARCH_PLANNER_ORCHESTRATOR

        config = SubAgentConfig(
            name="research_planner_orchestrator",
            system_prompt=system_prompt,
            permissions=plan_permissions(),
            output_model=PlanOutput,
            max_turns=50,           # P10.3: Planner iteration cap
            use_thinking=True,      # P10.11: extended reasoning
            thinking_budget_tokens=10000,
            effort="high",
        )

        # SDK backstop: min(node_alloc * 2, node_alloc + 2.5% of total) [HG-7]
        node_alloc = self._budget.remaining_node(node_id)
        backstop = compute_sdk_backstop(node_alloc, self._settings.max_budget_usd)

        output, cost = await invoke_agent(
            config=config,
            node_context=node_context,
            model=self._settings.model,
            sdk_budget_backstop_usd=backstop,
            cwd=self._repo_path,      # Plan composite uses primary checkout
            dag_run_id=dag_run_id,
            node_id=node_id,
        )

        if not isinstance(output, PlanOutput):
            raise AgentError(
                f"PlanComposite expected PlanOutput, got {type(output).__name__}"
            )
        return output, cost
```

**Determining `is_consolidation`:** The executor passes `is_consolidation=True` when the Plan node has parent nodes that include Review-type nodes. Specifically: a Plan node is consolidation if any of its `parent_node_ids` references a node with `type == NodeType.REVIEW`. The L0 Plan node has no parents (or only the issue as implicit input), so `is_consolidation=False`.

### ChildDAGSpec Validation

When the Plan composite returns a `PlanOutput` with a non-null `child_dag`, the executor (or orchestrator) must validate the spec before building the child DAG:

```python
def validate_child_dag_spec(spec: ChildDAGSpec) -> None:
    """Validate ChildDAGSpec per P02 rules. Raises ValueError on violation."""
    n = len(spec.composites)
    if n >= 8:
        raise ValueError(f"P02 violation: {n} composites (max 7; 8+ rejected)")
    if n >= 6 and not spec.justification:
        raise ValueError(f"P02 violation: {n} composites without justification")
    if n < 1:
        raise ValueError("ChildDAGSpec must have at least 1 composite")

    ids = {c.id for c in spec.composites}
    suffixes = [c.branch_suffix for c in spec.composites]
    if len(set(suffixes)) != len(suffixes):
        raise ValueError("Duplicate branch_suffix in ChildDAGSpec")

    for edge in spec.sequential_edges:
        if edge.from_composite_id not in ids:
            raise ValueError(f"Unknown from_composite_id: {edge.from_composite_id}")
        if edge.to_composite_id not in ids:
            raise ValueError(f"Unknown to_composite_id: {edge.to_composite_id}")
```

This function lives in `src/agent_agent/agents/plan.py`.

### Tests for Phase 4b

#### `tests/unit/test_plan_composite.py`

```
Tests:
1. validate_child_dag_spec accepts 2-5 composites with no justification
2. validate_child_dag_spec requires justification for 6-7 composites
3. validate_child_dag_spec rejects 8+ composites
4. validate_child_dag_spec rejects duplicate branch_suffix
5. validate_child_dag_spec rejects unknown edge references
6. PlanComposite.execute() called with is_consolidation=False uses RESEARCH_PLANNER_ORCHESTRATOR prompt
7. PlanComposite.execute() called with is_consolidation=True uses CONSOLIDATION_PLANNER prompt
8. PlanComposite config: max_turns=50, use_thinking=True [P10.3, P10.11]
9. PlanComposite permissions: read-only tools only [P3.3]
```

Tests 6-9 mock `invoke_agent` to verify the `SubAgentConfig` passed to it.

#### `tests/component/test_plan_composite.py`

```
@pytest.mark.sdk  — real SDK calls; $1 hard budget cap

Tests:
1. L0 plan: given a simple issue + fixture repo, returns PlanOutput with valid ChildDAGSpec
2. Consolidation plan: given approved ReviewOutputs, returns PlanOutput with child_dag=None
3. PlanOutput.discoveries contains at least one discovery (smoke test)

Fixture: repo_with_remote fixture; a hand-crafted issue description
Budget: sdk_budget_backstop_usd = 1.0 per test
```

### Gate

Unit tests and component tests pass. The Plan composite produces Pydantic-valid `PlanOutput` with real SDK calls.

---

## Phase 4c — Coding Composite

This is the most complex sub-phase. The Coding composite is a DAG container [P10.2] that runs an iterative nested DAG: up to `max_cycles` (default 3) cycles of `Programmer -> Test Designer -> Test Executor -> Debugger`.

### Files to Create/Modify

#### `src/agent_agent/agents/coding.py`

```python
"""Coding composite — iterative nested DAG of sub-agents.

Internal cycle (max 3 cycles) [P10.4]:
  Programmer -> Test Designer -> Test Executor -> Debugger

Each cycle is a 4-node acyclic DAG persisted before execution [P1.8].
Sub-agent outputs are persisted after each step for resumption [P10.5].
Programmer and Debugger handle own git staging/committing [P10.13].
Push-on-exit is composite-level [P10.13].
"""
from __future__ import annotations

import asyncio
import json
import subprocess

import structlog

from ..budget import BudgetManager
from ..config import Settings
from ..dag.executor import AgentError
from ..models.agent import AgentOutput, AgentTestOutput, CodeOutput
from ..models.context import NodeContext
from ..models.dag import DAGNode, DAGRun, NodeType
from ..observability import EventType, emit_event
from ..state import StateStore
from ..worktree import WorktreeRecord
from .base import SubAgentConfig, compute_sdk_backstop, invoke_agent
from .prompts import PROGRAMMER, TEST_DESIGNER, TEST_EXECUTOR, DEBUGGER
from .tools import (
    programmer_permissions,
    test_designer_permissions,
    test_executor_permissions,
    debugger_permissions,
)

_logger = structlog.get_logger(__name__)

MAX_CYCLES = 3  # P10.4: max Coding composite cycles


class CodingComposite:
    """Executes a Coding composite node with iterative nested DAG cycles.

    The composite owns the worktree lifecycle (already created by the executor)
    and performs push-on-exit.
    """

    def __init__(
        self,
        settings: Settings,
        state: StateStore,
        budget: BudgetManager,
        worktree: WorktreeRecord,
        repo_path: str,
        issue_number: str,
        node_id: str,
    ) -> None:
        self._settings = settings
        self._state = state
        self._budget = budget
        self._worktree = worktree
        self._repo_path = repo_path
        self._issue_number = issue_number
        self._node_id = node_id  # needed for push failure state update

    async def execute(
        self,
        node_context: NodeContext,
        dag_run_id: str,
        node_id: str,
    ) -> tuple[CodeOutput, float]:
        """Run the Coding composite's iterative nested DAG.

        Returns the final (CodeOutput, total_cost_usd) from the composite.
        Push-on-exit is performed regardless of success/failure [P1.11].

        Invariant: CodeOutput.branch_name must always equal self._worktree.branch.
        The composite forces this in the final CodeOutput construction. The review
        worktree checkout relies on the branch_name stored via update_dag_node_worktree(),
        which is set from self._worktree.branch at worktree creation time.
        """
        total_cost = 0.0
        last_code_output: CodeOutput | None = None
        last_test_output: AgentTestOutput | None = None
        cycle_history: list[dict] = []

        try:
            for cycle in range(MAX_CYCLES):
                emit_event(
                    EventType.NODE_STARTED, dag_run_id,
                    node_id=node_id,
                    cycle=cycle + 1,
                    max_cycles=MAX_CYCLES,
                )

                # --- Programmer ---
                programmer_output, cost = await self._invoke_programmer(
                    node_context, dag_run_id, node_id, cycle,
                    last_test_output=last_test_output,
                )
                total_cost += cost
                last_code_output = programmer_output
                # Persist sub-agent output for resumption [P10.5]
                await self._persist_sub_agent_output(
                    dag_run_id, node_id, cycle, "programmer", programmer_output
                )

                # --- Test Designer ---
                test_plan_output, cost = await self._invoke_test_designer(
                    node_context, dag_run_id, node_id, cycle,
                    code_output=programmer_output,
                )
                total_cost += cost
                await self._persist_sub_agent_output(
                    dag_run_id, node_id, cycle, "test_designer", test_plan_output
                )

                # --- Test Executor ---
                test_results, cost = await self._invoke_test_executor(
                    node_context, dag_run_id, node_id, cycle,
                    test_plan=test_plan_output,
                )
                total_cost += cost

                # --- Post-Test Executor validation: net-zero source changes [P3.3] ---
                # The Test Executor may write temp files during execution, but the
                # net effect on tracked (committed) files must be zero.
                await self._validate_no_source_modifications(dag_run_id, node_id, cycle)

                last_test_output = test_results
                await self._persist_sub_agent_output(
                    dag_run_id, node_id, cycle, "test_executor", test_results
                )

                cycle_history.append({
                    "cycle": cycle + 1,
                    "tests_passed": test_results.passed,
                })

                # Check if tests pass -> done
                if test_results.passed:
                    last_code_output = CodeOutput(
                        summary=programmer_output.summary,
                        files_changed=programmer_output.files_changed,
                        branch_name=self._worktree.branch,
                        commit_sha=programmer_output.commit_sha,
                        tests_passed=True,
                        discoveries=programmer_output.discoveries,
                    )
                    break

                # Tests failed, cycles remain -> invoke Debugger
                if cycle + 1 < MAX_CYCLES:
                    debugger_output, cost = await self._invoke_debugger(
                        node_context, dag_run_id, node_id, cycle,
                        code_output=programmer_output,
                        test_results=test_results,
                    )
                    total_cost += cost
                    last_code_output = debugger_output
                    await self._persist_sub_agent_output(
                        dag_run_id, node_id, cycle, "debugger", debugger_output
                    )

            # If we exhausted cycles without passing, set tests_passed=False
            if last_code_output and not last_code_output.tests_passed:
                last_code_output = CodeOutput(
                    summary=last_code_output.summary,
                    files_changed=last_code_output.files_changed,
                    branch_name=self._worktree.branch,
                    commit_sha=last_code_output.commit_sha,
                    tests_passed=False,
                    discoveries=last_code_output.discoveries,
                )

        finally:
            # Push-on-exit [P1.11/P10.13] — always push regardless of success/failure
            await self._push_branch()

        if last_code_output is None:
            # Should not happen, but handle gracefully
            last_code_output = CodeOutput(
                summary="Coding composite produced no output",
                files_changed=[],
                branch_name=self._worktree.branch,
                commit_sha=None,
                tests_passed=False,
            )

        return last_code_output, total_cost
```

**Sub-agent invocation methods** (all private methods on `CodingComposite`):

```python
    async def _invoke_programmer(
        self,
        node_context: NodeContext,
        dag_run_id: str,
        node_id: str,
        cycle: int,
        last_test_output: AgentTestOutput | None,
    ) -> tuple[CodeOutput, float]:
        system_prompt = PROGRAMMER.format(worktree_path=self._worktree.path)

        # On cycle > 0, include previous test failure in context
        # by augmenting node_context.parent_outputs
        augmented_context = self._augment_context(
            node_context, last_test_output, cycle
        )

        config = SubAgentConfig(
            name="programmer",
            system_prompt=system_prompt,
            permissions=programmer_permissions(self._worktree.path),
            output_model=CodeOutput,
            max_turns=40,   # P10.3
        )

        output, cost = await invoke_agent(
            config=config,
            node_context=augmented_context,
            model=self._settings.model,
            sdk_budget_backstop_usd=compute_sdk_backstop(
                self._budget.remaining_node(node_id), self._settings.max_budget_usd
            ),
            cwd=self._worktree.path,
            dag_run_id=dag_run_id,
            node_id=f"{node_id}-cycle{cycle}-programmer",
        )
        if not isinstance(output, CodeOutput):
            raise AgentError(f"Programmer expected CodeOutput, got {type(output).__name__}")
        return output, cost

    async def _invoke_test_designer(
        self,
        node_context: NodeContext,
        dag_run_id: str,
        node_id: str,
        cycle: int,
        code_output: CodeOutput,
    ) -> tuple[AgentTestOutput, float]:
        # Add Programmer's CodeOutput to context
        augmented = self._augment_context_with_output(
            node_context, "programmer", code_output
        )

        config = SubAgentConfig(
            name="test_designer",
            system_prompt=TEST_DESIGNER,
            permissions=test_designer_permissions(),
            output_model=AgentTestOutput,
            max_turns=20,   # P10.3
        )

        output, cost = await invoke_agent(
            config=config,
            node_context=augmented,
            model=self._settings.model,
            sdk_budget_backstop_usd=compute_sdk_backstop(
                self._budget.remaining_node(node_id), self._settings.max_budget_usd
            ),
            cwd=self._worktree.path,
            dag_run_id=dag_run_id,
            node_id=f"{node_id}-cycle{cycle}-test_designer",
        )
        if not isinstance(output, AgentTestOutput):
            raise AgentError(f"TestDesigner expected AgentTestOutput, got {type(output).__name__}")
        return output, cost

    async def _invoke_test_executor(
        self,
        node_context: NodeContext,
        dag_run_id: str,
        node_id: str,
        cycle: int,
        test_plan: AgentTestOutput,
    ) -> tuple[AgentTestOutput, float]:
        system_prompt = TEST_EXECUTOR.format(worktree_path=self._worktree.path)
        augmented = self._augment_context_with_output(
            node_context, "test_designer", test_plan
        )

        config = SubAgentConfig(
            name="test_executor",
            system_prompt=system_prompt,
            permissions=test_executor_permissions(self._worktree.path),
            output_model=AgentTestOutput,
            max_turns=15,   # P10.3
        )

        output, cost = await invoke_agent(
            config=config,
            node_context=augmented,
            model=self._settings.model,
            sdk_budget_backstop_usd=compute_sdk_backstop(
                self._budget.remaining_node(node_id), self._settings.max_budget_usd
            ),
            cwd=self._worktree.path,
            dag_run_id=dag_run_id,
            node_id=f"{node_id}-cycle{cycle}-test_executor",
        )
        if not isinstance(output, AgentTestOutput):
            raise AgentError(f"TestExecutor expected AgentTestOutput, got {type(output).__name__}")
        return output, cost

    async def _validate_no_source_modifications(
        self, dag_run_id: str, node_id: str, cycle: int,
    ) -> None:
        """Validate that the Test Executor did not net-modify tracked source files [P3.3].

        After Test Executor completes, run `git diff` in the worktree to check
        if any committed files were modified. Tests may create temp files (coverage
        reports, __pycache__, .pytest_cache) but must not change Programmer-committed
        source files. If modifications are detected, revert them with `git checkout .`
        and raise AgentError so the cycle can be retried or escalated.
        """
        result = await asyncio.to_thread(
            subprocess.run,
            ["git", "diff", "--name-only", "HEAD"],
            cwd=self._worktree.path,
            capture_output=True,
            text=True,
        )

        modified_files = [f for f in result.stdout.strip().split("\n") if f]
        if modified_files:
            _logger.warning(
                "coding_composite.test_executor_modified_files",
                dag_run_id=dag_run_id,
                node_id=node_id,
                cycle=cycle,
                modified_files=modified_files,
            )
            emit_event(
                EventType.TOOL_DENIED,
                dag_run_id,
                node_id=f"{node_id}-cycle{cycle}-test_executor",
                reason="test_executor_modified_source_files",
                files=modified_files,
            )
            # Revert source file changes to restore Programmer's committed state
            await asyncio.to_thread(
                subprocess.run,
                ["git", "checkout", "."],
                cwd=self._worktree.path,
                check=True,
            )
            raise AgentError(
                f"Test Executor modified tracked source files: {modified_files}. "
                "Changes reverted. Test results may be unreliable."
            )

    async def _invoke_debugger(
        self,
        node_context: NodeContext,
        dag_run_id: str,
        node_id: str,
        cycle: int,
        code_output: CodeOutput,
        test_results: AgentTestOutput,
    ) -> tuple[CodeOutput, float]:
        system_prompt = DEBUGGER.format(worktree_path=self._worktree.path)
        # Include both CodeOutput and TestOutput in context
        augmented = self._augment_context_with_outputs(
            node_context,
            {"programmer": code_output, "test_executor": test_results},
        )

        config = SubAgentConfig(
            name="debugger",
            system_prompt=system_prompt,
            permissions=debugger_permissions(self._worktree.path),
            output_model=CodeOutput,
            max_turns=20,   # P10.3
        )

        output, cost = await invoke_agent(
            config=config,
            node_context=augmented,
            model=self._settings.model,
            sdk_budget_backstop_usd=compute_sdk_backstop(
                self._budget.remaining_node(node_id), self._settings.max_budget_usd
            ),
            cwd=self._worktree.path,
            dag_run_id=dag_run_id,
            node_id=f"{node_id}-cycle{cycle}-debugger",
        )
        if not isinstance(output, CodeOutput):
            raise AgentError(f"Debugger expected CodeOutput, got {type(output).__name__}")
        return output, cost
```

**Context augmentation methods:**

```python
    def _augment_context(
        self,
        base: NodeContext,
        test_output: AgentTestOutput | None,
        cycle: int,
    ) -> NodeContext:
        """Add previous cycle's test results to parent_outputs for the Programmer."""
        if test_output is None or cycle == 0:
            return base
        augmented_outputs = dict(base.parent_outputs)
        augmented_outputs[f"prev-cycle-{cycle - 1}-test"] = test_output
        return base.model_copy(update={"parent_outputs": augmented_outputs})

    def _augment_context_with_output(
        self,
        base: NodeContext,
        key: str,
        output: AgentOutput,
    ) -> NodeContext:
        augmented_outputs = dict(base.parent_outputs)
        augmented_outputs[key] = output
        return base.model_copy(update={"parent_outputs": augmented_outputs})

    def _augment_context_with_outputs(
        self,
        base: NodeContext,
        outputs: dict[str, AgentOutput],
    ) -> NodeContext:
        augmented_outputs = dict(base.parent_outputs)
        augmented_outputs.update(outputs)
        return base.model_copy(update={"parent_outputs": augmented_outputs})
```

**Push and persistence methods:**

```python
    async def _push_branch(self) -> None:
        """Push the worktree's branch to remote [P1.11].

        Push is attempted regardless of success/failure. If push fails,
        log the error but do not raise — the composite still returns its output.
        """
        if not self._settings.git_push_enabled:
            _logger.info(
                "coding_composite.push_skipped",
                reason="git_push_enabled=False",
                branch=self._worktree.branch,
            )
            return

        last_exc: subprocess.CalledProcessError | None = None
        for attempt in range(2):  # 1 attempt + 1 retry [HG-1]
            try:
                await asyncio.to_thread(
                    subprocess.run,
                    ["git", "push", "-u", "origin", self._worktree.branch],
                    cwd=self._worktree.path,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                _logger.info(
                    "coding_composite.push_success",
                    branch=self._worktree.branch,
                    attempt=attempt + 1,
                )
                return  # success
            except subprocess.CalledProcessError as exc:
                last_exc = exc
                if attempt == 0:
                    _logger.warning(
                        "coding_composite.push_retry",
                        branch=self._worktree.branch,
                        stderr=exc.stderr,
                    )
                    await asyncio.sleep(5)  # wait before retry

        # Both attempts failed — set branch_name = None so review gate catches it
        _logger.error(
            "coding_composite.push_failed",
            branch=self._worktree.branch,
            stderr=last_exc.stderr if last_exc else "unknown",
            attempts=2,
        )
        await self._state.update_dag_node_worktree(
            self._node_id, self._worktree.path, None
        )

    async def _persist_sub_agent_output(
        self,
        dag_run_id: str,
        node_id: str,
        cycle: int,
        sub_agent: str,
        output: AgentOutput,
        attempt: int = 0,
    ) -> None:
        """Persist sub-agent output for resumption [P10.5].

        Stores as a SharedContext entry keyed by composite_node_id + cycle + sub_agent + attempt.
        The attempt number prevents INSERT conflicts when a sub-agent is retried
        within the same cycle due to transient retry.

        Note: Sub-agent persistence in Phase 4 is audit/debugging only, not functional
        resumption. The read path for reconstructing composite state from persisted
        sub-agent outputs is a Phase 6 concern.
        """
        await self._state.append_shared_context(
            entry_id=f"{node_id}-cycle{cycle}-{sub_agent}-attempt{attempt}",
            dag_run_id=dag_run_id,
            source_node_id=node_id,
            category="sub_agent_output",
            data={
                "composite_node_id": node_id,
                "cycle": cycle,
                "sub_agent": sub_agent,
                "output": json.loads(output.model_dump_json()),
            },
        )
```

### Tests for Phase 4c

#### `tests/unit/test_coding_composite.py`

```
Tests (all mock invoke_agent):
1. CodingComposite runs 1 cycle when tests pass on first try
2. CodingComposite runs up to MAX_CYCLES when tests keep failing
3. CodingComposite skips Debugger on the last cycle (no more cycles remaining)
4. Push-on-exit is called in finally block (even on exception)
5. Push is skipped when git_push_enabled=False
6. Sub-agent outputs are persisted after each step [P10.5]
7. Context augmentation: cycle > 0 includes previous test results
8. Programmer config: max_turns=40, write permissions [P10.3, P3.3]
9. Test Designer config: max_turns=20, read-only [P10.3, P3.3]
10. Test Executor config: max_turns=15, test execution perms (worktree + no git mutations) [P10.3, P3.3]
11. Debugger config: max_turns=20, write permissions [P10.3, P3.3]
12. Branch name in output matches worktree.branch
13. Push failure: mock subprocess.run to raise CalledProcessError; verify composite still returns CodeOutput, push failure is logged, and branch_name set to None in state store
14. Post-test git diff validation: mock git diff to return modified files → AgentError raised, git checkout . called to revert [P3.3]
15. Post-test git diff validation: mock git diff to return empty → no error, cycle continues normally
```

#### `tests/component/test_coding_composite.py`

```
@pytest.mark.sdk  — real SDK calls; $1 hard budget cap per test

Tests:
1. Full cycle: Programmer writes code, Test Executor runs tests, returns CodeOutput
2. Push verified: after composite exits, branch exists on bare remote
3. Sub-agent outputs persisted in state store [P10.5]

Fixture: repo_with_remote + coding worktree pre-created
Budget: sdk_budget_backstop_usd = 1.0

Note: All component test fixtures that use worktrees must call `git worktree prune`
in the target repo at fixture setup to prevent stale worktree conflicts from prior
crashed test runs.
```

### Gate

Unit tests pass with mocked SDK. Component tests pass with real SDK calls and verify push to bare remote.

---

## Phase 4d — Review Composite

### Files to Create

#### `src/agent_agent/agents/review.py`

```python
"""Review composite — Reviewer sub-agent on a read-only worktree.

Dispatched after the paired Coding composite pushes its branch [P01].
Reads CodeOutput + TestOutput from all cycles.
Read-only tools [P3.3] — enforced by tool selection, not filesystem perms.
"""
from __future__ import annotations

from ..budget import BudgetManager
from ..config import Settings
from ..dag.executor import AgentError
from ..models.agent import ReviewOutput
from ..models.context import NodeContext
from ..worktree import WorktreeRecord
from .base import SubAgentConfig, compute_sdk_backstop, invoke_agent
from .prompts import REVIEWER
from .tools import reviewer_permissions


class ReviewComposite:
    """Executes a Review composite node (single Reviewer invocation)."""

    def __init__(
        self,
        settings: Settings,
        worktree: WorktreeRecord,
        budget: BudgetManager,
    ) -> None:
        self._settings = settings
        self._worktree = worktree
        self._budget = budget

    async def execute(
        self,
        node_context: NodeContext,
        dag_run_id: str,
        node_id: str,
    ) -> tuple[ReviewOutput, float]:
        """Invoke Reviewer and return (ReviewOutput, cost_usd)."""
        system_prompt = REVIEWER.format(worktree_path=self._worktree.path)

        config = SubAgentConfig(
            name="reviewer",
            system_prompt=system_prompt,
            permissions=reviewer_permissions(self._worktree.path),
            output_model=ReviewOutput,
            max_turns=20,           # P10.3: Review iteration cap
        )

        # SDK backstop: min(node_alloc * 2, node_alloc + 2.5% of total) [HG-7]
        node_alloc = self._budget.remaining_node(node_id)
        backstop = compute_sdk_backstop(node_alloc, self._settings.max_budget_usd)

        output, cost = await invoke_agent(
            config=config,
            node_context=node_context,
            model=self._settings.model,
            sdk_budget_backstop_usd=backstop,
            cwd=self._worktree.path,    # Review reads from worktree
            dag_run_id=dag_run_id,
            node_id=node_id,
        )

        if not isinstance(output, ReviewOutput):
            raise AgentError(f"Reviewer expected ReviewOutput, got {type(output).__name__}")
        return output, cost
```

### Tests for Phase 4d

#### `tests/unit/test_review_composite.py`

```
Tests (all mock invoke_agent):
1. ReviewComposite config: max_turns=20, read-only tools [P10.3, P3.3]
2. ReviewComposite.execute() returns (ReviewOutput, cost_usd)
3. System prompt includes worktree path
4. Reviewer permissions: no write tools, no git mutation tools
```

#### `tests/component/test_review_composite.py`

```
@pytest.mark.sdk  — real SDK calls; $1 hard budget cap

Tests:
1. Given a pushed branch with real code changes, Reviewer returns valid ReviewOutput
2. ReviewOutput.verdict is one of: approved, needs_rework, rejected
3. ReviewOutput.findings is a list of ReviewFinding objects

Fixture: repo_with_remote + branch with committed changes + review worktree
Budget: sdk_budget_backstop_usd = 1.0
```

### Gate

Unit tests pass. Component tests produce a valid ReviewOutput from a real branch.

---

## Phase 4e — Executor Wiring + Child DAG Recursion

This sub-phase wires everything together: replacing the stub `AgentFn` with real composite invocations, and implementing child DAG spawning.

### Files to Modify

#### `src/agent_agent/dag/executor.py` — Changes

**1. Replace `AgentFn` with composite dispatch.**

Add a new `CompositeDispatcher` class (or extend `DAGExecutor`) that replaces the generic `agent_fn` callback:

```python
# New import at top of executor.py
from ..agents.plan import PlanComposite, validate_child_dag_spec
from ..agents.coding import CodingComposite
from ..agents.review import ReviewComposite
from ..worktree import WorktreeManager, WorktreeRecord
```

Add a `CompositeDispatcher` to `DAGExecutor.__init__`:

```python
class DAGExecutor:
    def __init__(
        self,
        state: StateStore,
        budget: BudgetManager,
        context_provider: ContextProvider,
        agent_fn: AgentFn | None,          # Still accepted for backward compat (tests)
        settings: Settings,
        *,
        worktree_manager: WorktreeManager | None = None,
        repo_path: str | None = None,
        issue_number: str | None = None,
    ) -> None:
        self._state = state
        self._budget = budget
        self._ctx = context_provider
        self._agent_fn = agent_fn
        self._settings = settings
        self._worktree_mgr = worktree_manager
        self._repo_path = repo_path
        self._issue_number = issue_number or "0"
        self._worktrees: dict[str, WorktreeRecord] = {}  # node_id -> WorktreeRecord
        self._coding_counter = 0
        self._review_counter = 0
```

**Complete updated `_dispatch_node()` signature** (for reference):

```python
async def _dispatch_node(
    self, dag_run: DAGRun, node: DAGNode, all_nodes: list[DAGNode]
) -> bool:
```

(This signature is unchanged from Phase 2/3; it already receives `dag_run` and `all_nodes`.)

**2. New method `_dispatch_composite()`** — replaces `self._agent_fn(node, context)` when `self._agent_fn is None`:

```python
    async def _dispatch_composite(
        self, node: DAGNode, context: NodeContext, dag_run: DAGRun, all_nodes: list[DAGNode]
    ) -> tuple[AgentOutput, float]:
        """Dispatch a real composite agent (Phase 4 replacement for stub AgentFn)."""
        if node.type == NodeType.PLAN:
            return await self._dispatch_plan(node, context, dag_run, all_nodes)
        elif node.type == NodeType.CODING:
            return await self._dispatch_coding(node, context, dag_run)
        elif node.type == NodeType.REVIEW:
            return await self._dispatch_review(node, context, dag_run, all_nodes)
        else:
            raise ValueError(f"Unknown node type: {node.type}")

    async def _dispatch_plan(
        self, node: DAGNode, context: NodeContext, dag_run: DAGRun, all_nodes: list[DAGNode]
    ) -> tuple[AgentOutput, float]:
        # Determine if consolidation: any parent is a Review node
        is_consolidation = any(
            n.type == NodeType.REVIEW
            for n in all_nodes
            if n.id in node.parent_node_ids
        )
        composite = PlanComposite(
            settings=self._settings,
            repo_path=dag_run.repo_path,
            budget=self._budget,
        )
        return await composite.execute(
            node_context=context,
            dag_run_id=dag_run.id,
            node_id=node.id,
            is_consolidation=is_consolidation,
        )

    async def _dispatch_coding(
        self, node: DAGNode, context: NodeContext, dag_run: DAGRun
    ) -> tuple[AgentOutput, float]:
        if self._worktree_mgr is None:
            raise ValueError("WorktreeManager required for Coding nodes")
        if self._repo_path is None:
            raise ValueError("repo_path required for Coding nodes")

        self._coding_counter += 1
        worktree = await self._worktree_mgr.create_coding_worktree(
            repo_path=self._repo_path,
            dag_run_id=dag_run.id,
            node_id=node.id,
            n=self._coding_counter,
        )
        self._worktrees[node.id] = worktree

        # Update node with worktree_path
        await self._state.update_dag_node_worktree(node.id, worktree.path, worktree.branch)

        try:
            composite = CodingComposite(
                settings=self._settings,
                state=self._state,
                budget=self._budget,
                worktree=worktree,
                repo_path=self._repo_path,
                issue_number=self._issue_number,
                node_id=node.id,
            )
            output, cost = await composite.execute(
                node_context=context,
                dag_run_id=dag_run.id,
                node_id=node.id,
            )
            return output, cost
        finally:
            # Teardown worktree after composite completes
            await self._worktree_mgr.remove_worktree(self._repo_path, worktree.path)

    async def _dispatch_review(
        self, node: DAGNode, context: NodeContext, dag_run: DAGRun, all_nodes: list[DAGNode]
    ) -> tuple[AgentOutput, float]:
        if self._worktree_mgr is None:
            raise ValueError("WorktreeManager required for Review nodes")
        if self._repo_path is None:
            raise ValueError("repo_path required for Review nodes")

        # Find the paired Coding node's branch
        coding_node = None
        for parent_id in node.parent_node_ids:
            for n in all_nodes:
                if n.id == parent_id and n.type == NodeType.CODING:
                    coding_node = n
                    break

        if coding_node is None:
            raise AgentError(f"Review node {node.id} has no Coding parent")
        db_node = await self._state.get_dag_node(coding_node.id)
        if db_node is None or db_node.branch_name is None:
            raise AgentError(f"Coding node {coding_node.id} has no branch_name set")

        self._review_counter += 1
        worktree = await self._worktree_mgr.create_review_worktree(
            repo_path=self._repo_path,
            dag_run_id=dag_run.id,
            node_id=node.id,
            n=self._review_counter,
            existing_branch=db_node.branch_name,
        )
        self._worktrees[node.id] = worktree

        try:
            composite = ReviewComposite(
                settings=self._settings,
                worktree=worktree,
                budget=self._budget,
            )
            output, cost = await composite.execute(
                node_context=context,
                dag_run_id=dag_run.id,
                node_id=node.id,
            )
            return output, cost
        finally:
            await self._worktree_mgr.remove_worktree(self._repo_path, worktree.path)
```

**3. Update `_dispatch_node()` to use composites when `agent_fn` is None:**

In the `_run_with_transient_retry` method, change the agent invocation line:

```python
# Before (Phase 2/3):
output, cost_usd = await self._agent_fn(node, context)

# After (Phase 4):
if self._agent_fn is not None:
    output, cost_usd = await self._agent_fn(node, context)
else:
    output, cost_usd = await self._dispatch_composite(node, context, dag_run, all_nodes)
```

**Signature update note:** `dag_run` is already a parameter of `_run_with_transient_retry`; only `all_nodes` needs to be added. The updated signature is:

```python
async def _run_with_transient_retry(
    self, dag_run: DAGRun, node: DAGNode, all_nodes: list[DAGNode], reruns_used: int
) -> tuple[NodeResult | None, Exception | None, FailureCategory | None]:
```

All existing callers of `_run_with_transient_retry` (within `_dispatch_node()`) must pass the `all_nodes` parameter. All existing tests that construct `DAGExecutor` must be updated with the new optional kwargs (`worktree_manager`, `repo_path`, `issue_number`, all defaulting to `None`).

**4. Replace the `NotImplementedError` with child DAG recursion:**

Replace the existing block in `_dispatch_node`:

```python
# BEFORE:
if isinstance(result.output, PlanOutput) and result.output.child_dag is not None:
    raise NotImplementedError(
        "Child DAG spawning is not implemented in Phase 2. "
        "Wire recursive dispatch in Phase 4."
    )

# AFTER:
if isinstance(result.output, PlanOutput) and result.output.child_dag is not None:
    await self._spawn_child_dag(dag_run, node, result.output.child_dag, all_nodes)
```

**5. Implement `_spawn_child_dag()`:**

```python
    async def _spawn_child_dag(
        self,
        parent_dag_run: DAGRun,
        plan_node: DAGNode,
        child_dag_spec: ChildDAGSpec,
        parent_all_nodes: list[DAGNode],
    ) -> None:
        """Build and execute a child DAG from a PlanOutput's ChildDAGSpec.

        Enforces the 4-level nesting cap [P1.10].
        Persists all child DAG nodes before execution [P1.8].
        """
        from ..models.agent import ChildDAGSpec
        from .engine import topological_sort

        # Validate the spec [P02]
        try:
            validate_child_dag_spec(child_dag_spec)
        except ValueError as exc:
            raise AgentError(
                f"Plan agent produced invalid ChildDAGSpec: {exc}"
            ) from exc

        # Determine current nesting level
        current_level = plan_node.level
        child_level = current_level + 1
        if child_level > 4:
            raise ResourceExhaustionError(
                f"DAG nesting depth limit reached: level {child_level} > 4 [P1.10]"
            )

        # Build child DAG nodes from the ChildDAGSpec
        child_nodes = self._build_child_dag_nodes(
            dag_run=parent_dag_run,
            spec=child_dag_spec,
            level=child_level,
            plan_node_id=plan_node.id,
        )

        # Persist all child nodes before execution [P1.8]
        for node in child_nodes:
            await self._state.create_dag_node(node)

        # Allocate budget for child nodes
        self._budget.allocate_child([n.id for n in child_nodes])

        # Execute the child DAG (recursive call)
        child_ordered = topological_sort(child_nodes)
        for child_node in child_ordered:
            current_run = await self._state.get_dag_run(parent_dag_run.id)
            if current_run and current_run.status in (
                DAGRunStatus.PAUSED, DAGRunStatus.FAILED, DAGRunStatus.ESCALATED,
            ):
                break

            if not await self._can_dispatch(child_node, child_nodes):
                await self._state.update_dag_node_status(child_node.id, NodeStatus.FAILED.value)
                await self._state.update_dag_run_status(
                    parent_dag_run.id, DAGRunStatus.FAILED.value,
                    error=f"Review gate blocked for child node {child_node.id}",
                )
                return

            success = await self._dispatch_node(parent_dag_run, child_node, child_nodes)
            if not success:
                return

            await self._drain_and_flush(parent_dag_run.id)

            if self._budget.should_pause():
                self._budget.record_pause()
                await self._drain_and_flush(parent_dag_run.id)
                await self._state.update_dag_run_status(
                    parent_dag_run.id, DAGRunStatus.PAUSED.value
                )
                return

    def _build_child_dag_nodes(
        self,
        dag_run: DAGRun,
        spec: ChildDAGSpec,
        level: int,
        plan_node_id: str,
    ) -> list[DAGNode]:
        """Build DAGNode list from ChildDAGSpec.

        Structure per P01: for each CompositeSpec, create (Coding, Review) pair.
        Add terminal Plan node. Apply sequential edges.
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        run_id = dag_run.id
        nodes: list[DAGNode] = []
        coding_ids: dict[str, str] = {}   # composite_id -> coding node id
        review_ids: dict[str, str] = {}   # composite_id -> review node id

        # Build parent_node_ids lists fully BEFORE constructing DAGNode instances.
        # This avoids in-place mutation of Pydantic model fields after construction,
        # which would break if DAGNode ever uses frozen=True.

        # Step 1: Collect IDs
        for comp in spec.composites:
            coding_id = f"{run_id}-l{level}-coding-{comp.id}"
            review_id = f"{run_id}-l{level}-review-{comp.id}"
            coding_ids[comp.id] = coding_id
            review_ids[comp.id] = review_id

        terminal_plan_id = f"{run_id}-l{level}-plan-terminal"
        all_review_ids = list(review_ids.values())

        # Step 2: Build coding parent lists (include sequential edges)
        coding_parents: dict[str, list[str]] = {}
        for comp in spec.composites:
            coding_parents[comp.id] = [plan_node_id]
        for edge in spec.sequential_edges:
            from_review_id = review_ids[edge.from_composite_id]
            coding_parents[edge.to_composite_id].append(from_review_id)

        # Step 3: Construct all DAGNode instances with final parent lists
        for comp in spec.composites:
            coding_id = coding_ids[comp.id]
            review_id = review_ids[comp.id]

            coding_node = DAGNode(
                id=coding_id,
                dag_run_id=run_id,
                type=NodeType.CODING,
                level=level,
                composite_id=comp.id,
                parent_node_ids=coding_parents[comp.id],
                child_node_ids=[review_id],
                created_at=now,
                updated_at=now,
            )
            nodes.append(coding_node)

            review_node = DAGNode(
                id=review_id,
                dag_run_id=run_id,
                type=NodeType.REVIEW,
                level=level,
                composite_id=f"{comp.id}-review",
                parent_node_ids=[coding_id],
                child_node_ids=[terminal_plan_id],
                created_at=now,
                updated_at=now,
            )
            nodes.append(review_node)

        # Terminal Plan node — depends on all Review nodes
        terminal_plan = DAGNode(
            id=terminal_plan_id,
            dag_run_id=run_id,
            type=NodeType.PLAN,
            level=level,
            composite_id=f"L{level}-terminal",
            parent_node_ids=all_review_ids,
            child_node_ids=[],
            created_at=now,
            updated_at=now,
        )
        nodes.append(terminal_plan)

        # Note: child_node_ids is not authoritative for cross-level edges.
        # Only parent_node_ids is traversed by the executor for dispatch ordering.
        # child_node_ids is set here for structural completeness but the executor
        # relies on parent_node_ids for topological sort and dependency checking.

        return nodes
```

#### `src/agent_agent/budget.py` — Add `allocate_child()`

```python
    MIN_NODE_BUDGET_USD = 0.10  # minimum viable budget per child node

    def allocate_child(self, node_ids: list[str]) -> None:
        """Allocate remaining budget equally across child DAG nodes.

        Called when a child DAG is spawned. Uses the remaining budget
        (not the total budget) for the split.

        Note: allocate() is one-shot for root nodes only; allocate_child()
        is for child DAGs. The guard in allocate() is intentional — it
        assumes it is the only root allocation method.

        Raises ResourceExhaustionError if per-node budget < MIN_NODE_BUDGET_USD.
        """
        remaining = self.remaining_dag()
        if remaining <= 0 or not node_ids:
            return
        per_node = remaining / len(node_ids)
        if per_node < self.MIN_NODE_BUDGET_USD:
            raise ResourceExhaustionError(
                f"Insufficient budget for child DAG: ${per_node:.4f} per node "
                f"(minimum ${self.MIN_NODE_BUDGET_USD}), {len(node_ids)} nodes, "
                f"${remaining:.4f} remaining"
            )
        for node_id in node_ids:
            self._allocations[node_id] = per_node
            self._used[node_id] = 0.0
            self._log(
                node_id,
                BudgetEventType.INITIAL_ALLOCATION,
                remaining,
                remaining,
                f"child DAG allocation: ${per_node:.4f} per node ({len(node_ids)} nodes)",
            )
```

#### `src/agent_agent/state.py` — Add `update_dag_node_worktree()`

Add a method to update a node's worktree_path and branch_name:

```python
    async def update_dag_node_worktree(
        self, node_id: str, worktree_path: str, branch_name: str
    ) -> None:
        """Update a DAG node with its assigned worktree path and branch."""
        async with self._db() as db:
            await db.execute(
                "UPDATE dag_nodes SET worktree_path = ?, branch_name = ?, updated_at = ? WHERE id = ?",
                (worktree_path, branch_name, datetime.now(timezone.utc).isoformat(), node_id),
            )
            await db.commit()
```

#### `src/agent_agent/orchestrator.py` — Wire Real Composites

Update the `Orchestrator` to pass `worktree_manager`, `repo_path`, and `issue_number` to the executor when `agent_fn` is None. Also add worktree cleanup at the start of `run()`:

```python
    async def run(self) -> tuple[str, str]:
        # 0. Prune orphaned worktrees from prior crashed runs
        # This prevents git worktree name conflicts during development/testing.
        # A simple `git worktree prune` suffices for Phase 4; Phase 6 adds a
        # full startup scan with orphan detection.
        await asyncio.to_thread(
            subprocess.run,
            ["git", "worktree", "prune"],
            cwd=self._repo_path,
            capture_output=True,
            check=False,  # non-fatal if prune fails
        )

        # ... existing code through step 5 ...

        # 5. Set up budget, context provider, executor
        budget = BudgetManager(dag_run_id=run_id, total_budget_usd=self._settings.max_budget_usd)

        worktree_mgr = None
        if self._agent_fn is None:
            from .worktree import from_settings
            worktree_mgr = from_settings(self._settings.worktree_base_dir)

        ctx_provider = ContextProvider(
            shared_context=shared_context,
            budget=budget,
            state=self._state,
            settings=self._settings,
        )
        executor = DAGExecutor(
            state=self._state,
            budget=budget,
            context_provider=ctx_provider,
            agent_fn=self._agent_fn,
            settings=self._settings,
            worktree_manager=worktree_mgr,
            repo_path=self._repo_path,
            issue_number=self._extract_issue_number(),
        )

        # ... rest of run() unchanged ...

    def _extract_issue_number(self) -> str:
        """Extract issue number from the issue URL."""
        # https://github.com/owner/repo/issues/123 -> "123"
        parts = self._issue_url.rstrip("/").split("/")
        if parts and parts[-1].isdigit():
            return parts[-1]
        return "0"
```

### Tests for Phase 4e

#### `tests/unit/test_executor_phase4.py`

```
Tests:
1. _dispatch_composite routes PLAN nodes to PlanComposite
2. _dispatch_composite routes CODING nodes to CodingComposite (with worktree creation)
3. _dispatch_composite routes REVIEW nodes to ReviewComposite (with review worktree)
4. Child DAG spawn: PlanOutput with child_dag triggers _spawn_child_dag
5. Child DAG spawn: nesting level incremented correctly
6. Child DAG spawn: level > 4 raises ResourceExhaustionError [P1.10]
7. _build_child_dag_nodes: creates (Coding, Review) pairs + terminal Plan
8. _build_child_dag_nodes: sequential edges wire Review -> Coding dependencies
9. _build_child_dag_nodes: terminal Plan depends on all Review nodes
10. allocate_child: splits remaining budget (not total) across child nodes
11. Backward compat: executor with agent_fn still works (Phase 2/3 tests unbroken)
12. is_consolidation detection: Plan node with Review parent -> True
13. is_consolidation detection: Plan node with no Review parent -> False
```

#### `tests/component/test_e2e_phase4.py`

```
@pytest.mark.sdk  — real SDK calls; $1 hard budget cap

Tests:
1. Full two-level flow [HG-11]: L0 Plan → L1 (Coding→Review→Plan) → L2 (Coding→Review→Plan)
   The flow is: Plan decomposes issue, Coding implements, Review finds issues,
   terminal Plan spawns rework child DAG, second Coding fixes, second Review approves,
   terminal Plan returns null (done).
2. Branch pushed to bare remote before Review dispatch (both levels)
3. All node statuses updated correctly in state store (both levels)
4. Budget events recorded for all nodes across both levels
5. Child DAG nodes persisted before execution at each level [P1.8]

Fixture: repo_with_remote + hand-crafted issue that requires one rework iteration
Budget: sdk_budget_backstop_usd = 1.0
```

### Gate

All existing Phase 2/3 tests still pass (backward compatibility via `agent_fn`). Phase 4e unit tests pass. Component tests demonstrate full two-level flow (L0→L1→L2) with real SDK calls: plan→code→review→plan→code→review→plan [HG-11].

---

## SharedContext Wire-up (4e addendum)

The `shared.py` file already handles discovery appending. The only change needed is that real agents now produce actual discoveries (not empty lists). No code changes to `shared.py` are required — the existing `append_discoveries()` function in `src/agent_agent/context/shared.py` already:

1. Validates discovery type via the Pydantic discriminator
2. Appends `DiscoveryRecord` with provenance
3. Persists to state store
4. Logs potential conflicts (last-write-wins, Phase 6 adds full resolution)

The executor already calls `append_discoveries()` after each node completes (see `executor.py` line ~294). No wiring changes needed.

---

## Cross-Cutting Concerns

### Observability

All tool calls are logged via `emit_event(EventType.TOOL_CALLED, ...)` and `emit_event(EventType.TOOL_DENIED, ...)` within the `can_use_tool` callback in `base.py` [P8.6, P11]. Every sub-agent invocation within a composite uses a node_id that encodes the composite node + cycle + sub-agent name (e.g., `{node_id}-cycle0-programmer`), providing full traceability.

### Budget

- `sdk_budget_backstop_usd` is computed via `compute_sdk_backstop(node_allocation, total_budget)` = `min(node_alloc * 2, node_alloc + total_budget * 0.025)` — a runaway-prevention backstop proportional to the node's allocation, not the total budget [HG-7]. `BudgetManager.record_usage()` is the real enforcement; the SDK backstop is a safety net.
- `ResultMessage.total_cost_usd` from the SDK is the ground truth for budget tracking. The executor calls `budget.record_usage(node_id, cost_usd)` after each node.
- Child DAG budget allocation uses remaining budget, not total budget (`allocate_child()`).

### Error Handling Summary

| Layer | Error | Handling |
|-------|-------|----------|
| SDK `query()` | `ProcessError` (rate limit / network) | Catch, inspect exit code/stderr, raise `TransientError` |
| SDK `query()` | `ProcessError` (auth failure) | Catch, raise `DeterministicError` |
| SDK `query()` | `CLIConnectionError` / `CLINotFoundError` | Catch, raise `DeterministicError` |
| `ResultMessage` | `subtype="error_max_budget_usd"` | Check `is_error`, raise `ResourceExhaustionError` |
| `ResultMessage` | `max_turns` reached (no output) | Check for `None` result/structured_output, raise `ResourceExhaustionError` |
| `output_format` | Pydantic validation / JSON parse failure | Raise `AgentError` |
| `can_use_tool` | Returns `PermissionResultDeny` | SDK feeds denial message back to agent; logged as `TOOL_DENIED`; 5 denials → `interrupt=True` stops session, raise `SafetyViolationError` |
| `git push` | Subprocess failure (2 attempts) | Log error, set `branch_name = None` in state store so review gate catches it, do not raise |
| Child DAG spawn | Level > 4 | Raise `ResourceExhaustionError` |
| Child DAG spawn | Invalid ChildDAGSpec | Raise `AgentError` (Plan produced bad spec) |

---

## Items Requiring Human Guidance

The following items were flagged during technical and policy review as ambiguous or risky, requiring human judgment. Each includes an auto-decision that will be used if no human guidance is received before implementation.

### HG-1: Push failure handling (P1.11)

> Policy review AMBIGUOUS #1 / Technical review Item 21

P1.11 states push-on-exit is required for crash recoverability, and the Violations section of P01 lists "exiting without pushing" as a violation. The plan now sets `branch_name = None` on push failure so the review gate catches it, but this means the Coding node effectively fails silently (returns CodeOutput but review cannot proceed).

> **[HG-1 RESOLVED]:** Option (c) with a single wait-and-retry. `_push_branch()` now attempts the push twice with a 5-second wait between attempts. If both fail, sets `branch_name = None` to block the review gate. This handles transient network issues while keeping the MVP simple.

### HG-2: 25% SharedContextView cap enforcement location (P5.8)

> Policy review AMBIGUOUS #2

The plan's `serialize_node_context()` serializes the entire `SharedContextView` without checking whether it exceeds the 25% cap. The existing `ContextProvider.build_context()` is expected to enforce this.

> **[HG-2 RESOLVED]:** The cap is enforced in `ContextProvider.build_context()` (via `BudgetManager.shared_context_cap()`), which truncates/filters the view before it reaches `serialize_node_context()`. No change to `serialize_node_context()` is needed.

### HG-3: max_tokens / budget enforcement (P7.7) [RESOLVED — policy updated]

> Policy review AMBIGUOUS #3

P7.7 has been updated to use `max_budget_usd` for budget enforcement instead of requiring `max_tokens`. The plan correctly omits `max_tokens` from `ClaudeAgentOptions` and uses `max_budget_usd` (the SDK backstop) as the enforcement mechanism. No change needed.

### HG-4: Crash recovery reconstruction logic (P10.5)

> Policy review AMBIGUOUS #4

Sub-agent output persistence is in place (write path), but the reconstruction code path is absent. `CodingComposite.execute()` does not detect and skip already-completed sub-agents on restart.

> **[HG-4 RESOLVED]:** Write-only persistence is sufficient for Phase 4. Full reconstruction deferred to Phase 6 (documented in deferred items table).

### HG-5: Bash path validation fragility

> Technical review Item 9

The `_validate_worktree_path()` regex `r'/[\w/.-]+'` is fragile: fails on paths with spaces, matches partial strings, misses relative path escapes.

> **[HG-5 RESOLVED]:** Whitelist approach implemented. `_validate_worktree_path()` now uses a `SAFE_PREFIXES` whitelist (worktree root, /usr/, /bin/, /etc/, /tmp/, /dev/*, /proc/, /sys/). Absolute paths not matching the whitelist are rejected. Relative paths are allowed (they resolve within the cwd which is the worktree). Phase 6 adds more sophisticated shell command parsing.

### HG-6: Read-only Bash blocklist limitations

> Technical review Item 10

`_validate_read_only_bash()` uses substring matching against a blocklist. It will not catch writes via Python one-liners, piped redirects with file descriptors, or `tee` variants.

> **[HG-6 RESOLVED]:** Accept limitations for Phase 4 MVP. Plan and Test Designer agents are low-risk. Known limitations documented.

### HG-7: SDK backstop budget multiplier (2x total per sub-agent)

> Technical review Item 12

> **[HG-7 RESOLVED]:** Backstop formula changed from `total_budget * 2` to `min(node_allocation * 2, node_allocation + total_budget * 0.025)`. This bounds each sub-agent invocation proportionally to its node's allocation while capping the overshoot at 2.5% of the total budget. `compute_sdk_backstop()` helper added to `base.py`. All composite call sites updated.

### HG-8: SDK `output_format` contract verification

> Technical review Item 13

The plan builds `output_format` as `{"type": "json", "schema": model.model_json_schema()}`. The SDK may require a different format or wrapper.

> **[HG-8 RESOLVED]:** Verify during SDK API prerequisite. Fall back to system prompt enforcement if `output_format` is unsupported.

### HG-9: Minimum viable budget per child node

> Technical review Item 23

If the parent Plan node consumed significant budget, child nodes might each get `< $0.10` — potentially insufficient for a real SDK invocation.

> **[HG-9 RESOLVED]:** `MIN_NODE_BUDGET_USD = 0.10` check added to `allocate_child()`. Raises `ResourceExhaustionError` if per-node budget is insufficient.

### HG-10: $1 budget cap per component test

> Technical review Item 27

With real SDK calls (especially extended reasoning), $1 may be insufficient for meaningful tests.

> **[HG-10 RESOLVED]:** Use `haiku` model for component tests. $1 budget cap per test. Increase to $2 if flaky.

### HG-11: Multi-level recursion test coverage [RESOLVED]

> Technical review Item 28

**Human override applied:** Phase 4 component tests now exercise two full levels of recursion (L0 → L1 → L2): plan→code→review→plan→code→review→plan. The unit test for `level > 4 raises ResourceExhaustionError` covers the nesting cap.

### HG-12: serialize_node_context parseability

> Technical review Item 29

No test verifies that the custom markdown-like serialization format is parseable by the SDK agent.

> **[HG-12 RESOLVED]:** No explicit parsing test for Phase 4. Component tests verify implicitly.

---

## What Is Deferred to Phase 6

| Item | Why Deferred |
|------|-------------|
| Full policy context in system prompts | Requires estimation of what policy context to provide; Phase 6 adds policy reviewer agent |
| Sophisticated worktree path validation in Bash | Current regex heuristic is conservative (see known limitations below); Phase 6 adds shell command parsing |
| Structured escalation messages | Phase 6 fills the escalation stub with attempt history, DAG impact, recovery menu |
| Stage-aware budget pause | Phase 6: continue Review composites at 5% remaining; Phase 4 uses flat halt |
| Context overflow (masking + summarization) | Phase 4 inherits truncation-only from Phase 2; Phase 6 adds full P5.8 cascade |
| Conflict detection in SharedContext | Phase 4 inherits last-write-wins from Phase 2; Phase 6 adds confidence/recency resolution |
| Weighted budget allocation | Phase 4 uses equal split + remaining split for children; Phase 6 adds Plan-requested allocation |
| Composite crash resumption read path | Sub-agent output persistence is write-only in Phase 4 (audit/debugging). The read path for reconstructing composite state from persisted sub-agent outputs and continuing from the last completed step is a Phase 6 concern. |
| Multi-level recursion testing | [RESOLVED — HG-11] Phase 4 now tests two levels (L0 → L1 → L2) including a rework iteration. |

**Known limitations of Phase 4 Bash validation (`_validate_read_only_bash`, `_validate_worktree_path`):**
- `_validate_read_only_bash` uses substring matching; will not catch writes via Python one-liners, piped redirects with file descriptors, or `tee` variants
- `_validate_worktree_path` whitelist approach may produce false negatives (missing relative path escapes like `cd ../../../etc/passwd`); absolute paths not in `SAFE_PREFIXES` are rejected
- These are acceptable for Phase 4 MVP because: (a) Plan and Test Designer agents are low-risk (no worktree mutation consequences), (b) the real guard is that the Reviewer's worktree is not the source of truth, and (c) the Coding composite's pushed branch is the authoritative artifact

---

## File Summary

### New Files (Phase 4)

| File | Sub-phase | Purpose |
|------|-----------|---------|
| `src/agent_agent/agents/__init__.py` | 4a | Package init |
| `src/agent_agent/agents/base.py` | 4a | `invoke_agent()`, `SubAgentConfig`, `ToolPermission`, `serialize_node_context()` |
| `src/agent_agent/agents/tools.py` | 4a | Tool permission definitions per sub-agent type, argument validators |
| `src/agent_agent/agents/prompts.py` | 4a | System prompt templates for all sub-agent types |
| `src/agent_agent/agents/plan.py` | 4b | `PlanComposite`, `validate_child_dag_spec()` |
| `src/agent_agent/agents/coding.py` | 4c | `CodingComposite` with iterative nested DAG cycles |
| `src/agent_agent/agents/review.py` | 4d | `ReviewComposite` |
| `tests/unit/test_agents_base.py` | 4a | SDK wrapper unit tests |
| `tests/unit/test_plan_composite.py` | 4b | Plan composite unit tests |
| `tests/unit/test_coding_composite.py` | 4c | Coding composite unit tests |
| `tests/unit/test_review_composite.py` | 4d | Review composite unit tests |
| `tests/unit/test_executor_phase4.py` | 4e | Executor wiring + child DAG unit tests |
| `tests/component/test_sdk_wrapper.py` | 4a | Real SDK invocation tests |
| `tests/component/test_plan_composite.py` | 4b | Real SDK Plan composite tests |
| `tests/component/test_coding_composite.py` | 4c | Real SDK Coding composite tests |
| `tests/component/test_review_composite.py` | 4d | Real SDK Review composite tests |
| `tests/component/test_e2e_phase4.py` | 4e | Full two-level flow (L0→L1→L2) with real SDK |

### Modified Files (Phase 4)

| File | Sub-phase | Changes |
|------|-----------|---------|
| `src/agent_agent/dag/executor.py` | 4e | Add `_dispatch_composite()`, `_spawn_child_dag()`, `_build_child_dag_nodes()`; replace `NotImplementedError` |
| `src/agent_agent/budget.py` | 4e | Add `allocate_child()` method |
| `src/agent_agent/state.py` | 4e | Add `update_dag_node_worktree()` method |
| `src/agent_agent/orchestrator.py` | 4e | Wire `WorktreeManager` and real composites when `agent_fn is None` |
| `pyproject.toml` | 4a | Add `claude-agent-sdk` dependency |

---

## Build Order

```
4a  SDK wrapper + base agent + tools + prompts     (invoke_agent works in isolation)
4b  Plan composite                                  (PlanComposite → PlanOutput)
4c  Coding composite                                (CodingComposite → CodeOutput, with push)
4d  Review composite                                (ReviewComposite → ReviewOutput)
4e  Executor wiring + child DAG recursion           (full flow: Plan → Coding → Review → Plan)
```

Each sub-phase is independently committable. Phases 4a-4d can be tested in isolation with their own component tests. Phase 4e integrates everything and replaces the `NotImplementedError`.

---

## SDK Verification Addendum

*Verified: 2026-03-17 against `claude-agent-sdk` v0.1.49*

This section records the results of the SDK API prerequisite verification and all divergences from the original plan assumptions. Corrections have been applied inline throughout the plan.

### Verified API Surface

| Item | Plan Assumption | Verified Reality | Status |
|------|----------------|-----------------|--------|
| Package name | `claude-agent-sdk` | `claude-agent-sdk` (PyPI), import `claude_agent_sdk` | **Match** |
| Core API | `query(prompt, options)` → `AsyncIterator[Message]` | Confirmed | **Match** |
| Config class | `ClaudeAgentOptions` | Confirmed | **Match** |
| Result type | `ResultMessage` with `total_cost_usd` | Confirmed; also has `subtype`, `is_error`, `num_turns`, `structured_output`, `result` | **Match** (extended) |

### Divergences Corrected

| # | Area | Plan Had | SDK Has | Correction Applied |
|---|------|----------|---------|-------------------|
| 1 | `permission_mode` | `"never_ask"` | No `"never_ask"` in Python. Values: `"default"`, `"acceptEdits"`, `"bypassPermissions"`, `"plan"` | Use `"default"` + `can_use_tool` as sole gatekeeper. The SDK auto-sets `--permission-prompt-tool stdio` when `can_use_tool` is provided, routing all permission requests over JSON pipe — no interactive prompt. |
| 2 | `can_use_tool` signature | `async (tool_name: str, tool_input: dict) -> bool` | `async (tool_name: str, input_data: dict, context: ToolPermissionContext) -> PermissionResultAllow \| PermissionResultDeny` | Updated callback signature and return types. `PermissionResultDeny(message=..., interrupt=...)` feeds denial reason back to agent. |
| 3 | `thinking` config | `{"type": "enabled", "effort": "high"}` | `thinking` and `effort` are **separate fields**. `thinking: {"type": "enabled", "budget_tokens": int}`, `effort: "low"\|"medium"\|"high"\|"max"` | Split into `thinking_budget_tokens` and `effort` on `SubAgentConfig`. Options construction updated. |
| 4 | `output_format` | `{"type": "json", "schema": ...}` | `{"type": "json_schema", "schema": ...}` | Key changed from `"json"` to `"json_schema"`. |
| 5 | Structured output location | `result_message.content` | `result_message.structured_output` (when `output_format` used); text in `result_message.result` | Parse `structured_output` first, fall back to `result`. |
| 6 | Error: budget exceeded | SDK raises exception | `ResultMessage(subtype="error_max_budget_usd", is_error=True)` — not an exception | Check `is_error` and `subtype` after session ends. |
| 7 | Error: rate limit / auth | Typed exceptions (e.g., `RateLimitError`) | `ProcessError` with `.exit_code` and `.stderr` | Catch `ProcessError`, inspect exit code/stderr to classify. |
| 8 | Error: max_turns exceeded | SDK raises exception | Session ends normally; `ResultMessage.subtype="success"` but output may be `None` | Check for `None` result/structured_output after session. |
| 9 | Streaming requirement | Plain string prompt | `can_use_tool` requires prompt as `AsyncIterable` (streaming mode) | Wrap user message in async generator. |

### Permission Strategy — Confirmed Design

```python
permission_mode = "default"       # falls through to can_use_tool
allowed_tools = []                # nothing auto-approved — all tools go through callback
disallowed_tools = []             # nothing hard-denied — callback handles everything
can_use_tool = our_callback       # sole gatekeeper: allowlist + argument validation + logging
```

**Why this works headlessly:** When `can_use_tool` is set, the SDK automatically adds
`--permission-prompt-tool stdio` to the CLI subprocess. This routes all permission requests
over the stdin/stdout JSON pipe (not a TTY). The CLI sends a `control_request` with
`subtype: "can_use_tool"`, the SDK invokes the callback, and returns the decision. There is
no interactive prompt. The CLI is spawned with `stdin=PIPE, stdout=PIPE` — no TTY attached.

**Evaluation order in the CLI:**
1. Hooks (if any) — can allow, deny, or pass through
2. `disallowed_tools` — hard deny (empty for us)
3. `permission_mode` behavior — `"default"` does nothing (falls through)
4. `allowed_tools` — auto approve (empty for us)
5. `can_use_tool` callback — **our code decides**

**Denial strategy:**
- Single counter for all denials (unauthorized tool OR bad arguments on authorized tool)
- `PermissionResultDeny(message=..., interrupt=False)` for denials 1-4 — agent sees the reason and can self-correct
- `PermissionResultDeny(message=..., interrupt=True)` on denial 5 — SDK stops session immediately
- After session ends, `invoke_agent()` checks `denial_count >= 5` and raises `SafetyViolationError`
- Node outputs are saved before the error propagates (the `_persist_sub_agent_output` call in the composite's cycle loop handles this for completed sub-agents; the interrupted sub-agent has no output to save)

### SDK Exception Hierarchy

```
ClaudeSDKError (base)
├── CLIConnectionError
│   └── CLINotFoundError
├── ProcessError          (.exit_code, .stderr)
└── CLIJSONDecodeError
```

Limit-exceeded conditions are NOT exceptions — they are `ResultMessage` fields:
- Budget exceeded: `ResultMessage(subtype="error_max_budget_usd", is_error=True)`
- Max turns: session ends with `subtype="success"` but may have no output
