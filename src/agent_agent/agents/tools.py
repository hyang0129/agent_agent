"""Tool allowlists for each sub-agent type.

Used with --print + --allowedTools: the CLI auto-denies any tool not in the list.
"""

from __future__ import annotations


def plan_allowed_tools() -> list[str]:
    """ResearchPlannerOrchestrator: read-only, no execution [P3.3]."""
    return ["Read", "Glob", "Grep"]


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
    """Reviewer: read-only, no execution [P3.3]."""
    return ["Read", "Glob", "Grep"]
