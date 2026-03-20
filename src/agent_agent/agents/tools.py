"""Tool allowlists and permission objects for each sub-agent type.

Used with --print + --allowedTools: the CLI auto-denies any tool not in the list.
ToolPermission objects provide validate_args callbacks for future executor enforcement [P8.5].
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolPermission:
    """Permission entry for a set of SDK tools.

    sdk_tool_names: the SDK tool names this permission covers.
    validate_args: optional callback (tool_name, tool_args) -> bool.
        Returns True if the call is allowed, False if it should be denied.
        None means unconditionally allowed.
    """

    sdk_tool_names: list[str]
    validate_args: Callable[[str, dict[str, Any]], bool] | None = field(default=None)


# ---------------------------------------------------------------------------
# Read-only Bash guard
# ---------------------------------------------------------------------------

_BASH_WRITE_PATTERNS: tuple[str, ...] = (
    "git commit",
    "git add",
    "git push",
    "git checkout",
    "git reset",
    "git rm",
    "git mv",
    "git stash",
    "git merge",
    "git rebase",
    "git cherry-pick",
    "git tag -d",
    "git branch -d",
    "git branch -D",
    " > ",
    " >> ",
    "rm ",
    "rmdir",
    " mv ",
    " cp ",
    "chmod",
    "chown",
    "dd ",
    "truncate",
    "tee ",
    "sudo",
    "pip install",
    "apt",
    "yum",
    "brew",
)


def _read_only_bash_validator(tool_name: str, tool_args: dict[str, Any]) -> bool:
    """Return False if the bash command appears to be a write operation."""
    command = tool_args.get("command", "")
    if not isinstance(command, str):
        return False
    cmd_lower = command.lower()
    for pattern in _BASH_WRITE_PATTERNS:
        if pattern in cmd_lower:
            return False
    return True


def _make_read_only_bash_permission() -> ToolPermission:
    return ToolPermission(
        sdk_tool_names=["Bash"],
        validate_args=_read_only_bash_validator,
    )


# ---------------------------------------------------------------------------
# Tool lists
# ---------------------------------------------------------------------------


def plan_allowed_tools() -> list[str]:
    """ResearchPlannerOrchestrator: read-only + Bash (read-only guard) [P3.3]."""
    return ["Read", "Glob", "Grep", "Bash"]


def programmer_allowed_tools() -> list[str]:
    """Programmer: read + write + git within worktree [P3.3/P10.13]."""
    return ["Read", "Glob", "Grep", "Write", "Edit", "Bash"]


def tester_allowed_tools() -> list[str]:
    """Tester: read + write + run tests [P3.3]."""
    return ["Read", "Glob", "Grep", "Write", "Edit", "Bash"]


def debugger_allowed_tools() -> list[str]:
    """Debugger: same as Programmer [P3.3]."""
    return programmer_allowed_tools()


def reviewer_allowed_tools() -> list[str]:
    """Reviewer: read-only + Bash (read-only guard) [P3.3]."""
    return ["Read", "Glob", "Grep", "Bash"]


def policy_reviewer_allowed_tools() -> list[str]:
    """PolicyReviewer: read-only + Bash for git diff [P3.3]."""
    return reviewer_allowed_tools()


def test_designer_allowed_tools() -> list[str]:
    """TestDesigner: read-only + Bash (read-only guard) [P3.3]."""
    return ["Read", "Glob", "Grep", "Bash"]


def test_executor_allowed_tools() -> list[str]:
    """TestExecutor: read + write + run tests [P3.3]."""
    return ["Read", "Glob", "Grep", "Write", "Edit", "Bash"]


# ---------------------------------------------------------------------------
# Permission objects (for future executor-level enforcement [P8.5])
# ---------------------------------------------------------------------------


def reviewer_permissions(worktree_root: str) -> list[ToolPermission]:
    """Return ToolPermission objects for the Reviewer sub-agent.

    Two entries:
    - Read/Glob/Grep: unconditionally allowed.
    - Bash: allowed only for read-only commands (validated by callback).

    worktree_root is accepted for future path-scoping but not currently used.
    """
    return [
        ToolPermission(sdk_tool_names=["Read", "Glob", "Grep"], validate_args=None),
        _make_read_only_bash_permission(),
    ]


def policy_reviewer_permissions(worktree_root: str) -> list[ToolPermission]:
    """Return ToolPermission objects for the PolicyReviewer sub-agent.

    Identical to reviewer_permissions — same read-only constraints.
    """
    return reviewer_permissions(worktree_root)
