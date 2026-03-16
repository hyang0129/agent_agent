from __future__ import annotations

import os
from functools import lru_cache

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
    usd_per_byte: float = 0.0   # profiling placeholder; 0 disables USD-based context cap
    port: int = 8100
    max_workers: int = 1

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        if self.env == "test":
            return ":memory:"
        return f"data/{self.env}.db"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def configure_logging(settings: Settings | None = None) -> None:
    s = settings or get_settings()
    level = s.log_level.upper()

    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if s.log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(__import__("logging"), level)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
