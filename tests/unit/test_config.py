"""Tests for the config system."""
from __future__ import annotations

import os

import pytest

from agent_agent.config import Settings


def test_default_env_is_dev():
    s = Settings()
    assert s.env == os.environ.get("AGENT_AGENT_ENV", "dev")


def test_test_env_uses_memory_db(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_AGENT_ENV", "test")
    s = Settings(env="test")
    assert s.database_url == ":memory:"


def test_dev_env_uses_dev_db():
    s = Settings(env="dev")
    assert s.database_url == "data/dev.db"


def test_prod_env_uses_prod_db():
    s = Settings(env="prod")
    assert s.database_url == "data/prod.db"


def test_env_var_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_AGENT_MAX_BUDGET_USD", "2.50")
    s = Settings()
    assert s.max_budget_usd == pytest.approx(2.50)


def test_default_git_push_disabled():
    # Default is dev-safe: push disabled. Prod overrides via .env.prod.
    s = Settings()
    assert s.git_push_enabled is False


def test_default_dry_run_enabled():
    s = Settings()
    assert s.dry_run_github is True


def test_explicit_override_git_push(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_AGENT_GIT_PUSH_ENABLED", "true")
    s = Settings()
    assert s.git_push_enabled is True


def test_explicit_override_dry_run(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_AGENT_DRY_RUN_GITHUB", "false")
    s = Settings()
    assert s.dry_run_github is False


def test_default_port():
    s = Settings()
    assert s.port == 8100


def test_default_max_workers():
    s = Settings()
    assert s.max_workers == 1


def test_worktree_base_dir_defaults_to_none():
    # Not set by default — WorktreeManager raises if None at runtime
    s = Settings()
    assert s.worktree_base_dir is None


def test_worktree_base_dir_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_AGENT_WORKTREE_BASE_DIR", "/workspaces/.agent_agent_tests/worktrees")
    s = Settings()
    assert s.worktree_base_dir == "/workspaces/.agent_agent_tests/worktrees"
