"""Typer CLI — agent-agent run | status | bootstrap.

`run --issue <url> --repo <path>`:
  - Port conflict → exit with clear error
  - Self-repo rejection (--repo resolves to agent_agent's own tree)
  - CLAUDE.md presence validation (reads from local --repo path via filesystem)
  - Policy presence validation (docs/policies/POLICY_INDEX.md)
  - Constructs Orchestrator, calls run(), prints branch_name + summary

`status [run-id]`:
  - Reads DAG record directly from SQLite; no HTTP call

`bootstrap`:
  - Stub: exit code 2 + message. MUST NOT suggest running agent-agent bootstrap.
"""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import typer

from .config import Settings, configure_logging, get_settings

app = typer.Typer(name="agent-agent", add_completion=False)

# The source directory of this package — used for self-repo rejection
_SELF_DIR = Path(__file__).resolve().parent


def _resolve_repo(repo: str) -> Path:
    """Resolve and validate the --repo path."""
    p = Path(repo).resolve()
    if not p.is_dir():
        typer.echo(f"Error: --repo path does not exist or is not a directory: {p}", err=True)
        raise typer.Exit(code=1)
    return p


def _check_self_repo(repo_path: Path) -> None:
    """Reject if --repo resolves to agent_agent's own working tree."""
    # Walk up from _SELF_DIR to find the repo root (contains pyproject.toml)
    self_root = _SELF_DIR
    for _ in range(10):
        if (self_root / "pyproject.toml").exists():
            break
        self_root = self_root.parent

    try:
        repo_path.relative_to(self_root)
        typer.echo(
            "Error: --repo points to agent_agent's own installation directory. "
            "To use agent_agent to improve itself, clone the repo to a separate "
            "directory and pass that clone as --repo.",
            err=True,
        )
        raise typer.Exit(code=1)
    except ValueError:
        pass  # Not a subpath — safe


def _check_claude_md(repo_path: Path) -> str:
    """Validate CLAUDE.md exists in the target repo. Returns its content."""
    claude_md = repo_path / "CLAUDE.md"
    if not claude_md.is_file():
        typer.echo(
            f"Error: target repo is missing CLAUDE.md at {claude_md}. "
            "Create a CLAUDE.md file in the repo root before running agent-agent.",
            err=True,
        )
        raise typer.Exit(code=1)
    return claude_md.read_text(encoding="utf-8")


def _check_policy_index(repo_path: Path) -> None:
    """Validate docs/policies/POLICY_INDEX.md exists in the target repo."""
    policy_index = repo_path / "docs" / "policies" / "POLICY_INDEX.md"
    if not policy_index.is_file():
        typer.echo(
            f"Error: target repo is missing docs/policies/POLICY_INDEX.md at {policy_index}. "
            "Create the required policy files manually. "
            "See the agent-agent documentation for the expected layout.",
            err=True,
        )
        raise typer.Exit(code=1)


def _check_port(port: int) -> None:
    """Check if the port is available. Exit with clear error if in use."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        typer.echo(
            f"Error: port {port} is already in use. "
            "Stop the other process or set AGENT_AGENT_PORT to a different port.",
            err=True,
        )
        raise typer.Exit(code=1)
    finally:
        sock.close()


@app.command()
def run(
    issue: str = typer.Option(..., help="GitHub issue URL"),
    repo: str = typer.Option(..., help="Path to the target repository"),
) -> None:
    """Start a DAG run for a GitHub issue."""
    settings = get_settings()
    configure_logging(settings)

    repo_path = _resolve_repo(repo)
    _check_self_repo(repo_path)
    claude_md_content = _check_claude_md(repo_path)
    _check_policy_index(repo_path)
    _check_port(settings.port)

    asyncio.run(_run_async(settings, str(repo_path), claude_md_content, issue))


async def _run_async(
    settings: Settings, repo_path: str, claude_md_content: str, issue_url: str
) -> None:
    """Async entry point for the run command."""
    from .orchestrator import Orchestrator
    from .state import open_state_store

    async with open_state_store(settings.database_url) as state:
        orchestrator = Orchestrator(
            settings=settings,
            repo_path=repo_path,
            claude_md_content=claude_md_content,
            issue_url=issue_url,
            state_store=state,
        )
        branch_name, summary = await orchestrator.run()

    typer.echo(f"Branch: {branch_name}")
    typer.echo(f"Summary: {summary}")


@app.command()
def status(
    run_id: str = typer.Argument(..., help="DAG run ID"),
) -> None:
    """Show DAG run status (reads directly from SQLite)."""
    settings = get_settings()
    configure_logging(settings)
    asyncio.run(_status_async(settings, run_id))


async def _status_async(settings: Settings, run_id: str) -> None:
    """Async entry point for the status command."""
    from .state import open_state_store

    async with open_state_store(settings.database_url) as state:
        dag_run = await state.get_dag_run(run_id)
        if dag_run is None:
            typer.echo(f"Error: DAG run {run_id!r} not found.", err=True)
            raise typer.Exit(code=1)

        nodes = await state.list_dag_nodes(run_id)

    typer.echo(f"Run:    {dag_run.id}")
    typer.echo(f"Issue:  {dag_run.issue_url}")
    typer.echo(f"Status: {dag_run.status.value}")
    typer.echo(f"Budget: ${dag_run.usd_used:.4f} / ${dag_run.budget_usd:.4f}")
    typer.echo(f"Nodes:  {len(nodes)}")
    for n in nodes:
        typer.echo(f"  {n.id}  [{n.type.value}]  {n.status.value}")


@app.command()
def bootstrap() -> None:
    """Initialize target repo with CLAUDE.md and policy scaffolding.

    NOTE: This command is not yet implemented.
    """
    typer.echo("bootstrap is not yet implemented. Run `agent-agent --help` for available commands.")
    raise typer.Exit(code=2)
