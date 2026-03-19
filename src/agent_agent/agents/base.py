"""SDK wrapper — invoke_agent() for all sub-agent types.

Wraps the Claude Code Agent SDK. Enforces iteration caps, tool allowlists,
structured output parsing, and cost tracking.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal, cast

import structlog
from pydantic import BaseModel, ValidationError

from ..dag.executor import (
    AgentError,
    DeterministicError,
    ResourceExhaustionError,
    TransientError,
)
from ..models.agent import AgentOutput
from ..models.context import NodeContext
from claude_agent_sdk import (
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    ClaudeAgentOptions,
    ProcessError,
    ResultMessage,
    query,
)
from claude_agent_sdk.types import ThinkingConfigEnabled

_logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubAgentConfig:
    """Complete configuration for a sub-agent SDK invocation."""

    name: str  # e.g. "programmer", "debugger", "reviewer"
    system_prompt: str
    allowed_tools: list[str]  # tool whitelist; CLI auto-denies others via --print
    output_model: type[BaseModel]  # Pydantic model for structured output
    max_turns: int  # iteration cap per P10.3
    use_thinking: bool = False  # extended reasoning for Plan composite [P10.11]
    thinking_budget_tokens: int = 10000  # thinking budget when use_thinking=True
    effort: str = "high"  # "low", "medium", "high", "max"


# ---------------------------------------------------------------------------
# Budget backstop
# ---------------------------------------------------------------------------


def compute_sdk_backstop(node_allocation_usd: float, total_budget_usd: float) -> float:
    """Compute SDK budget backstop for a single sub-agent invocation [HG-7].

    Formula: min(node_allocation * 2, node_allocation + 2.5% of total_budget).
    This prevents runaway spending while giving each invocation reasonable headroom.
    The backstop is a safety net -- BudgetManager.record_usage() is the real enforcement.
    """
    return min(
        node_allocation_usd * 2,
        node_allocation_usd + total_budget_usd * 0.025,
    )


# ---------------------------------------------------------------------------
# NodeContext serialization
# ---------------------------------------------------------------------------


def serialize_node_context(ctx: NodeContext, role_hint: str) -> str:
    """Serialize NodeContext into the SDK user message.

    role_hint is a short string like 'programmer', 'reviewer' etc. used to
    label the context sections relevant to that role.
    """
    sections: list[str] = []

    # 1. Issue (always verbatim, never omitted) [P5.3]
    sections.append(
        f"## GitHub Issue\n\nURL: {ctx.issue.url}\nTitle: {ctx.issue.title}\n\n{ctx.issue.body}"
    )

    # 2. Repository context (always verbatim) [P5.3]
    sections.append(
        f"## Repository\n\n"
        f"Root directory (this is a directory — use Glob to explore files, not Read): {ctx.repo_metadata.path}\n"
        f"Default branch: {ctx.repo_metadata.default_branch}\n"
        f"Language: {ctx.repo_metadata.language or 'unknown'}\n"
        f"Framework: {ctx.repo_metadata.framework or 'unknown'}"
    )

    # 3. CLAUDE.md (always verbatim)
    sections.append(f"## Target Repo CLAUDE.md\n\n{ctx.repo_metadata.claude_md}")

    # 4. Parent outputs (typed Pydantic objects serialized as JSON)
    if ctx.parent_outputs:
        parts: list[str] = []
        for node_id, output in ctx.parent_outputs.items():
            parts.append(
                f"### Output from node {node_id}\n\n"
                f"```json\n{output.model_dump_json(indent=2)}\n```"
            )
        sections.append("## Upstream Outputs\n\n" + "\n\n".join(parts))

    # 5. Ancestor context (if any)
    if ctx.ancestor_context.entries:
        parts = []
        for entry in ctx.ancestor_context.entries:
            label = "(summarized)" if entry.summarized else "(full)"
            content = (
                entry.output
                if isinstance(entry.output, str)
                else entry.output.model_dump_json(indent=2)
            )
            parts.append(f"### Ancestor {entry.node_id} (depth {entry.depth}) {label}\n\n{content}")
        sections.append("## Ancestor Context\n\n" + "\n\n".join(parts))

    # 6. SharedContextView (discoveries, summary, active plan)
    scv = ctx.shared_context_view
    if scv.summary or scv.active_plan or scv.file_mappings or scv.root_causes:
        sc_parts: list[str] = []
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
                sc_parts.append(
                    f"### {category}\n"
                    + "\n".join(
                        f"- [{r.source_node_id}] {r.discovery.model_dump_json()}" for r in records
                    )
                )
        sections.append("## Shared Context\n\n" + "\n\n".join(sc_parts))

    return "\n\n---\n\n".join(sections)


# ---------------------------------------------------------------------------
# JSON extraction helpers
# ---------------------------------------------------------------------------


def _extract_json_from_text(text: str) -> Any | None:
    """Extract a JSON object from text that may contain markdown fences.

    Tries, in order:
    1. Content inside ```json ... ``` fences
    2. Content inside ``` ... ``` fences
    3. First { ... } block in the text

    Returns parsed JSON or None if extraction fails.
    """
    import re

    # Try ```json ... ```
    match = re.search(r"```json\s*\n(.*?)```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try ``` ... ```
    match = re.search(r"```\s*\n(.*?)```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try first { ... } block (greedy from first { to last })
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(text[first_brace : last_brace + 1])
        except json.JSONDecodeError:
            pass

    return None


# ---------------------------------------------------------------------------
# Streaming prompt wrapper
# ---------------------------------------------------------------------------


async def _prompt_iter(msg: str) -> AsyncIterator[dict[str, Any]]:
    """Wrap a user message as an async iterable for SDK streaming mode.

    The SDK streaming format requires:
        {"type": "user", "message": {"role": "user", "content": "..."}}
    """
    yield {"type": "user", "message": {"role": "user", "content": msg}}


# ---------------------------------------------------------------------------
# invoke_agent
# ---------------------------------------------------------------------------


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
    # 1. Serialize NodeContext into user message
    user_message = serialize_node_context(node_context, config.name)

    # 2. Build output_format
    output_format: dict[str, Any] = {
        "type": "json_schema",
        "schema": config.output_model.model_json_schema(),
    }

    # 3. Build ClaudeAgentOptions
    thinking_config: ThinkingConfigEnabled | None = None
    effort_value: Literal["low", "medium", "high", "max"] | None = None
    if config.use_thinking:
        thinking_config = ThinkingConfigEnabled(
            type="enabled", budget_tokens=config.thinking_budget_tokens
        )
        effort_value = cast(Literal["low", "medium", "high", "max"], config.effort)

    options = ClaudeAgentOptions(
        system_prompt=config.system_prompt,
        allowed_tools=config.allowed_tools,
        disallowed_tools=[],
        model=model,
        max_budget_usd=sdk_budget_backstop_usd,
        max_turns=config.max_turns,
        cwd=cwd,
        permission_mode=None,  # --print mode auto-denies tools not in allowed_tools list; no callback needed
        extra_args={"print": None},
        thinking=thinking_config,
        effort=effort_value,
        output_format=output_format,
    )

    # 4. Call query() and collect result
    try:
        result_message: ResultMessage | None = None
        prompt_source = _prompt_iter(user_message)

        async for message in query(prompt=prompt_source, options=options):
            _logger.debug(
                "sdk.turn",
                dag_run_id=dag_run_id,
                node_id=node_id,
                agent=config.name,
                msg_type=type(message).__name__,
                msg=str(message)[:2000],
            )
            if isinstance(message, ResultMessage):
                result_message = message

    except (CLIConnectionError, CLINotFoundError) as exc:
        raise DeterministicError(f"SDK CLI error: {type(exc).__name__}: {exc}") from exc
    except ProcessError as exc:
        # Inspect exit_code / stderr to classify
        stderr_text = (exc.stderr or "").lower()
        if "rate" in stderr_text or "timeout" in stderr_text or "network" in stderr_text:
            raise TransientError(
                f"SDK ProcessError (transient): exit_code={exc.exit_code}, stderr={exc.stderr}"
            ) from exc
        if "auth" in stderr_text or "api key" in stderr_text or "unauthorized" in stderr_text:
            raise DeterministicError(
                f"SDK ProcessError (auth): exit_code={exc.exit_code}, stderr={exc.stderr}"
            ) from exc
        # Default: treat unknown ProcessError as transient
        raise TransientError(
            f"SDK ProcessError (unknown): exit_code={exc.exit_code}, stderr={exc.stderr}"
        ) from exc
    except CLIJSONDecodeError:
        # Unknown — re-raise as-is for executor to classify as UNKNOWN [P10.7]
        raise

    if result_message is None:
        raise AgentError("SDK returned no messages (empty iterator)")

    # Check for SDK-reported errors
    if result_message.is_error:
        if result_message.subtype == "error_max_budget_usd":
            raise ResourceExhaustionError(
                f"Agent {config.name} exceeded SDK budget backstop: ${sdk_budget_backstop_usd:.2f}"
            )
        else:
            raise AgentError(
                f"SDK session ended with error: subtype={result_message.subtype}, "
                f"result={str(result_message.result)[:500]}"
            )

    cost_usd = result_message.total_cost_usd or 0.0

    # 5. Parse structured output
    raw_output = result_message.structured_output
    if raw_output is None:
        # Fallback: parse result text as JSON
        if result_message.result is None:
            raise ResourceExhaustionError(
                f"SDK returned no output (possible max_turns exhaustion): "
                f"subtype={result_message.subtype}, num_turns={result_message.num_turns}"
            )
        result_text = result_message.result
        # Try direct JSON parse first
        try:
            raw_output = json.loads(result_text)
        except json.JSONDecodeError:
            # Fallback: extract JSON from markdown fences (```json ... ```)
            # The SDK may return prose with embedded JSON when output_format
            # is not honored (e.g., with certain models or SDK versions).
            raw_output = _extract_json_from_text(result_text)
            if raw_output is None:
                raise AgentError(
                    f"Failed to parse SDK output as JSON (no structured_output, "
                    f"no valid JSON in result text). "
                    f"Raw result (truncated): {result_text[:500]}"
                )

    try:
        parsed_output = config.output_model.model_validate(raw_output)
    except ValidationError as exc:
        raise AgentError(
            f"SDK output failed Pydantic validation for {config.output_model.__name__}: {exc}"
        ) from exc

    # 6. Return (parsed_output, cost_usd)
    return parsed_output, cost_usd  # type: ignore[return-value]
