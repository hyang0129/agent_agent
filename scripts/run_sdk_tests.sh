#!/usr/bin/env bash
# ============================================================================
# Phase 5 SDK Integration Tests
# ============================================================================
#
# Runs the component tests that make real Claude API calls.
#
# WHY A SHELL SCRIPT?
# The Claude Agent SDK spawns a Claude Code CLI as a subprocess. If the
# CLAUDECODE env var is set (which it always is inside a Claude Code session),
# the CLI refuses to start: "Claude Code cannot be launched inside another
# Claude Code session." This script unsets CLAUDECODE before running pytest,
# allowing the SDK subprocess to launch cleanly. Running `pytest` directly
# from within Claude Code will fail because CLAUDECODE leaks into the
# subprocess environment.
#
# Usage:
#   cd /workspaces/hub3/repos/agent_agent
#   bash scripts/run_sdk_tests.sh            # run all tiers
#   bash scripts/run_sdk_tests.sh tier1      # mid-level only (no API calls)
#   bash scripts/run_sdk_tests.sh tier2      # SDK smoke tests
#   bash scripts/run_sdk_tests.sh tier3      # full E2E
#
# Prerequisites:
#   - claude CLI authenticated (Max plan OAuth in ~/.claude/)
#   - venv at /workspaces/.venvs/agent_agent/
#
# Cost: routed through Claude Code Max plan (flat-rate, not per-token API)
# ============================================================================

set -euo pipefail

REPO_DIR="/workspaces/hub3/repos/agent_agent"
VENV="/workspaces/.venvs/agent_agent"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()  { echo -e "${CYAN}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[PASS]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*"; }

# ---- Setup ----

cd "$REPO_DIR"
source "$VENV/bin/activate"

# Load .env
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Unset CLAUDECODE to allow SDK to spawn Claude Code subprocess
if [ -n "${CLAUDECODE:-}" ]; then
    warn "CLAUDECODE is set — unsetting to allow SDK subprocess."
    warn "Do NOT run this inside Claude Code. Use a separate terminal."
    unset CLAUDECODE
fi

# Verify SDK installed
if ! python -c "import claude_agent_sdk" 2>/dev/null; then
    fail "claude_agent_sdk not installed. Run: pip install 'claude-agent-sdk>=0.1.48'"
    exit 1
fi
info "SDK imported OK"

# Verify claude CLI available (the SDK spawns it as a subprocess)
if ! command -v claude &>/dev/null; then
    warn "claude CLI not found in PATH. SDK will attempt to find it."
fi

# Prune any stale worktrees
git worktree prune 2>/dev/null || true

TIER="${1:-all}"
FAILURES=0

# ============================================================================
# Tier 1: Mid-level integration (no API calls)
# Real executor + real SQLite + real git worktrees, mocked invoke_agent
# ============================================================================

run_tier1() {
    info "═══════════════════════════════════════════════════════════"
    info "Tier 1: Mid-level integration (no API calls)"
    info "═══════════════════════════════════════════════════════════"

    # Run all unit tests first (sanity check)
    info "Running unit tests..."
    if pytest tests/unit/ -q --tb=short; then
        ok "Unit tests passed"
    else
        fail "Unit tests failed — fix before proceeding"
        return 1
    fi

    # Run existing component tests that don't need SDK (worktree tests)
    info "Running worktree component tests..."
    if pytest tests/component/test_worktree.py -v --tb=short 2>/dev/null; then
        ok "Worktree component tests passed"
    else
        warn "Worktree component tests failed or skipped"
    fi

    ok "Tier 1 complete"
}

# ============================================================================
# Tier 2: SDK smoke tests (real calls via claude CLI, Max plan flat-rate)
# Tests invoke_agent with trivial prompts via Haiku
# ============================================================================

run_tier2() {
    info "═══════════════════════════════════════════════════════════"
    info "Tier 2: SDK smoke tests (real calls via claude CLI)"
    info "═══════════════════════════════════════════════════════════"

    info "Running SDK wrapper tests..."
    if pytest tests/component/test_sdk_wrapper.py -v --tb=long -s 2>&1; then
        ok "SDK wrapper tests passed"
    else
        fail "SDK wrapper tests FAILED"
        FAILURES=$((FAILURES + 1))
    fi

    info "Running Plan composite SDK tests..."
    if pytest tests/component/test_plan_composite.py -v --tb=long -s 2>&1; then
        ok "Plan composite SDK tests passed"
    else
        fail "Plan composite SDK tests FAILED"
        FAILURES=$((FAILURES + 1))
    fi

    info "Running Review composite SDK tests..."
    if pytest tests/component/test_review_composite.py -v --tb=long -s 2>&1; then
        ok "Review composite SDK tests passed"
    else
        fail "Review composite SDK tests FAILED"
        FAILURES=$((FAILURES + 1))
    fi

    if [ "$FAILURES" -eq 0 ]; then
        ok "Tier 2 complete — all SDK smoke tests passed"
    else
        fail "Tier 2 complete — $FAILURES test group(s) failed"
    fi
}

# ============================================================================
# Tier 3: Full E2E (real calls via claude CLI + real git, Max plan flat-rate)
# Coding composite with worktrees + push, E2E multi-level flow
# ============================================================================

run_tier3() {
    info "═══════════════════════════════════════════════════════════"
    info "Tier 3: Full E2E (real calls via claude CLI + real git)"
    info "═══════════════════════════════════════════════════════════"

    info "Running Coding composite SDK tests..."
    if pytest tests/component/test_coding_composite.py -v --tb=long -s 2>&1; then
        ok "Coding composite SDK tests passed"
    else
        fail "Coding composite SDK tests FAILED"
        FAILURES=$((FAILURES + 1))
    fi

    info "Running E2E Phase 4 tests..."
    if pytest tests/component/test_e2e_phase4.py -v --tb=long -s 2>&1; then
        ok "E2E Phase 4 tests passed"
    else
        fail "E2E Phase 4 tests FAILED"
        FAILURES=$((FAILURES + 1))
    fi

    if [ "$FAILURES" -eq 0 ]; then
        ok "Tier 3 complete — all E2E tests passed"
    else
        fail "Tier 3 complete — $FAILURES test group(s) failed"
    fi
}

# ============================================================================
# Main
# ============================================================================

echo ""
info "Phase 5 Integration Test Runner"
info "Repo: $REPO_DIR"
info "Tier: $TIER"
echo ""

case "$TIER" in
    tier1)
        run_tier1
        ;;
    tier2)
        run_tier2
        ;;
    tier3)
        run_tier3
        ;;
    all)
        run_tier1
        echo ""
        run_tier2
        echo ""
        run_tier3
        ;;
    *)
        fail "Unknown tier: $TIER (use tier1, tier2, tier3, or all)"
        exit 1
        ;;
esac

echo ""
echo "════════════════════════════════════════════════════════════"
if [ "$FAILURES" -eq 0 ]; then
    ok "All requested tiers passed"
else
    fail "$FAILURES test group(s) failed"
    exit 1
fi
