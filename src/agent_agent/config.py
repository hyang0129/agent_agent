from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV = os.environ.get("AGENT_AGENT_ENV", "dev")

_DEFAULT_MODEL: dict[str, str] = {
    "dev": "claude-haiku-4-5-20251001",
    "prod": "claude-sonnet-4-6",
    "test": "claude-haiku-4-5-20251001",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENT_AGENT_",
        env_file=(f".env.{_ENV}", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Defaults are dev-safe. Override via .env.prod or env vars for production.
    env: str = _ENV
    log_level: str = "DEBUG"
    log_format: str = "console"
    model: str = "claude-haiku-4-5-20251001"
    git_push_enabled: bool = False
    dry_run_github: bool = True
    max_budget_usd: float = 5.0
    usd_per_byte: float = 0.0  # profiling placeholder; 0 disables USD-based context cap
    worktree_base_dir: str | None = None  # required at runtime; WorktreeManager raises if None
    port: int = 8100
    max_workers: int = 1

    # PlanComposite tuning — disable thinking / lower turns for non-prod envs
    plan_use_thinking: bool = True
    plan_thinking_budget_tokens: int = 10000
    plan_effort: str = "high"
    plan_max_turns: int = 100

    # CodingComposite sub-agent turn caps
    programmer_max_turns: int = 100
    tester_max_turns: int = 115
    debugger_max_turns: int = 100
    reviewer_max_turns: int = 20

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        if self.env == "test":
            return ":memory:"
        return f"data/{self.env}.db"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


class _TeeFile:
    """Write to multiple file-like objects simultaneously."""

    def __init__(self, *files: Any) -> None:
        self._files = files

    def write(self, data: str) -> int:
        for f in self._files:
            f.write(data)
        return len(data)

    def flush(self) -> None:
        for f in self._files:
            f.flush()


# Module-level handle so the open file is not garbage-collected between calls.
_log_file_handle: Any = None


def configure_logging(settings: Settings | None = None, log_file: str | None = None) -> None:
    global _log_file_handle

    # Close any previously opened log file.
    if _log_file_handle is not None:
        try:
            _log_file_handle.close()
        except Exception:
            pass
        _log_file_handle = None

    s = settings or get_settings()
    level = s.log_level.upper()

    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if s.log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    if log_file is not None:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        _log_file_handle = open(log_file, "w", encoding="utf-8")  # noqa: SIM115
        logger_factory: Any = structlog.WriteLoggerFactory(
            file=_TeeFile(sys.stdout, _log_file_handle)
        )
    else:
        logger_factory = structlog.PrintLoggerFactory()

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(getattr(__import__("logging"), level)),
        context_class=dict,
        logger_factory=logger_factory,
        # Disable caching only when a log_file is supplied (i.e. test reconfiguration).
        # Production calls configure_logging once at startup and benefits from caching.
        cache_logger_on_first_use=log_file is None,
    )
