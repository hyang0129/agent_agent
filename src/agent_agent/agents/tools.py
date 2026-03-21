"""Tool allowlists and permission profiles for each sub-agent type.

Used with --print + --allowedTools: the CLI auto-denies any tool not in the list.
ToolPermission adds argument-level validation callbacks [P3.4/P8.5].
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ToolPermission:
    """Permission profile for a set of tools, with optional argument-level validation [P3.4/P8.5]."""

    sdk_tool_names: frozenset[str]
    validate_args: Callable[[str, dict[str, Any]], bool] | None = None


# ---------------------------------------------------------------------------
# Read-only bash validator (module-level for picklability)
# ---------------------------------------------------------------------------

_DENIED_BASH_PATTERNS = re.compile(
    r"git\s+commit|git\s+push|git\s+merge|git\s+rebase|git\s+reset\s+--hard|git\s+stash"
    r"|\brm\s|\bdd\s|chmod|chown|sudo|>{1,2}\s*|apt-get|pip\s+install|curl\s+-o|\bwget\b"
    r"|\btee\b|\bmkdir\b|\bmv\s|\bcp\s",
    re.IGNORECASE | re.MULTILINE,
)


def _read_only_bash_validator(tool_name: str, tool_args: dict[str, Any]) -> bool:
    """Allow safe read commands; block write/destructive operations."""
    command = tool_args.get("command", "")
    if not command:
        return False  # empty/missing command is not a valid read-only operation
    if _DENIED_BASH_PATTERNS.search(command):
        return False
    return True


def _make_worktree_bash_validator(worktree_root: str) -> Callable[[str, dict[str, Any]], bool]:
    """Return a closure for write agents that blocks git push and out-of-worktree paths."""

    def _validator(tool_name: str, tool_args: dict[str, Any]) -> bool:
        command = tool_args.get("command", "")
        # Block git push — orchestrator handles pushes
        if re.search(r"git\s+push", command, re.IGNORECASE):
            return False
        # Block absolute paths outside worktree_root
        # Heuristic: find tokens starting with / that don't start with worktree_root
        for token in command.split():
            clean = token.strip("'\"")
            if clean.startswith("/") and not clean.startswith(worktree_root):
                return False
        return True

    return _validator


def _make_write_path_validator(worktree_root: str) -> Callable[[str, dict[str, Any]], bool]:
    """Return a closure that restricts Write/Edit to paths within worktree_root [P8.5/P10.13].

    - Absolute paths not under worktree_root → denied
    - Relative paths → allowed (cwd is set to worktree_root for sub-agents)
    """

    def _validator(tool_name: str, tool_args: dict[str, Any]) -> bool:
        file_path = tool_args.get("file_path", "")
        if file_path.startswith("/") and not file_path.startswith(worktree_root):
            return False
        return True

    return _validator


# ---------------------------------------------------------------------------
# Permission functions
# ---------------------------------------------------------------------------


def plan_permissions() -> list[ToolPermission]:
    """ResearchPlannerOrchestrator: read-only, no execution [P3.3]."""
    return [
        ToolPermission(sdk_tool_names=frozenset({"Read", "Glob", "Grep"})),
        ToolPermission(sdk_tool_names=frozenset({"Bash"}), validate_args=_read_only_bash_validator),
    ]


def programmer_permissions(worktree_root: str) -> list[ToolPermission]:
    """Programmer: read + write + git within worktree [P3.3/P10.13]."""
    return [
        ToolPermission(sdk_tool_names=frozenset({"Read", "Glob", "Grep"})),
        ToolPermission(
            sdk_tool_names=frozenset({"Write", "Edit"}),
            validate_args=_make_write_path_validator(worktree_root),
        ),
        ToolPermission(
            sdk_tool_names=frozenset({"Bash"}),
            validate_args=_make_worktree_bash_validator(worktree_root),
        ),
    ]


def test_designer_permissions() -> list[ToolPermission]:
    """Test Designer: read-only, no execution [P3.3]."""
    return [
        ToolPermission(sdk_tool_names=frozenset({"Read", "Glob", "Grep"})),
        ToolPermission(sdk_tool_names=frozenset({"Bash"}), validate_args=_read_only_bash_validator),
    ]


def test_executor_permissions(worktree_root: str) -> list[ToolPermission]:
    """Test Executor: read + write + run tests [P3.3]."""
    return [
        ToolPermission(sdk_tool_names=frozenset({"Read", "Glob", "Grep"})),
        ToolPermission(
            sdk_tool_names=frozenset({"Write", "Edit"}),
            validate_args=_make_write_path_validator(worktree_root),
        ),
        ToolPermission(
            sdk_tool_names=frozenset({"Bash"}),
            validate_args=_make_worktree_bash_validator(worktree_root),
        ),
    ]


def debugger_permissions(worktree_root: str) -> list[ToolPermission]:
    """Debugger: same as Programmer [P3.3]."""
    return programmer_permissions(worktree_root)


def reviewer_permissions() -> list[ToolPermission]:
    """Reviewer: read-only tools + read-only Bash [P3.3]."""
    return [
        ToolPermission(sdk_tool_names=frozenset({"Read", "Glob", "Grep"})),
        ToolPermission(sdk_tool_names=frozenset({"Bash"}), validate_args=_read_only_bash_validator),
    ]


def policy_reviewer_permissions() -> list[ToolPermission]:
    """PolicyReviewer: same read-only constraints as Reviewer [P3.3]."""
    return reviewer_permissions()


# ---------------------------------------------------------------------------
# Legacy allowed_tools functions — kept for backward compatibility
# Deprecated — use *_permissions() instead
# ---------------------------------------------------------------------------


def plan_allowed_tools() -> list[str]:
    """ResearchPlannerOrchestrator: read-only, no execution [P3.3]."""
    return ["Read", "Glob", "Grep", "Bash"]


def programmer_allowed_tools() -> list[str]:
    """Programmer: read + write + git within worktree [P3.3/P10.13]."""
    return ["Read", "Glob", "Grep", "Write", "Edit", "Bash"]


def tester_allowed_tools() -> list[str]:
    """Tester: read + write + run tests [P3.3]."""
    return ["Read", "Glob", "Grep", "Write", "Edit", "Bash"]


def test_designer_allowed_tools() -> list[str]:
    """Test Designer: read-only [P3.3]."""
    return ["Read", "Glob", "Grep", "Bash"]


def test_executor_allowed_tools() -> list[str]:
    """Test Executor: read + write + run tests [P3.3]."""
    return ["Read", "Glob", "Grep", "Write", "Edit", "Bash"]


def debugger_allowed_tools() -> list[str]:
    """Debugger: same as Programmer [P3.3]."""
    return programmer_allowed_tools()


def reviewer_allowed_tools() -> list[str]:
    """Reviewer: read-only, no execution [P3.3]."""
    return ["Read", "Glob", "Grep", "Bash"]


def policy_reviewer_allowed_tools() -> list[str]:
    """PolicyReviewer: read-only + Bash for git diff [P3.3]."""
    return reviewer_allowed_tools()
