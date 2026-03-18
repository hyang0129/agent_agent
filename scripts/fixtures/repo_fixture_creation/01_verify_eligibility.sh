#!/usr/bin/env bash
# 01_verify_eligibility.sh — Check that a GitHub repo is eligible as a fixture source
#
# Usage:
#   TARGET_REPO=<owner/repo> AGENT_AGENT_FIXTURE_BOT_TOKEN=<token> \
#     ./scripts/fixtures/01_verify_eligibility.sh
#
# Required environment variables:
#   TARGET_REPO                    — full GitHub repo path, e.g. dbader/schedule
#   AGENT_AGENT_FIXTURE_BOT_TOKEN  — GitHub token for API access
#
# Checks:
#   1. License permits commercial use (blocks CC-NC, SSPL, BSL, no-license)
#   2. No vendored dependency tree (vendor/, Godeps/, third_party/, node_modules/, etc.)
#   3. Primary language is Python (only permitted codebase type for now)
#
# Exit 0 if the repo passes all checks.
# Exit 1 with a reason if the repo is ineligible.

set -euo pipefail

if [[ -z "${TARGET_REPO:-}" ]]; then
  echo "Usage: TARGET_REPO=<owner/repo> AGENT_AGENT_FIXTURE_BOT_TOKEN=<token> $0" >&2
  exit 1
fi

if [[ -z "${AGENT_AGENT_FIXTURE_BOT_TOKEN:-}" ]]; then
  echo "ERROR: AGENT_AGENT_FIXTURE_BOT_TOKEN is not set." >&2
  exit 1
fi

export GH_TOKEN="${AGENT_AGENT_FIXTURE_BOT_TOKEN}"

echo "Checking eligibility: ${TARGET_REPO} ..."
echo

ERRORS=()

# --- Check 1: license ---
SPDX=$(gh api "repos/${TARGET_REPO}/license" --jq '.license.spdx_id' 2>/dev/null || echo "NONE")

case "${SPDX}" in
  # Explicitly blocked
  CC-BY-NC|CC-BY-NC-SA|CC-BY-NC-ND|SSPL|BSL-1.0|NONE|NOASSERTION)
    ERRORS+=("license '${SPDX}' does not permit commercial use")
    ;;
  # Allow everything else (MIT, BSD-*, Apache-2.0, ISC, MPL-2.0, GPL-*, LGPL-*, AGPL-*, CC-BY, CC-BY-SA, ...)
  *)
    echo "  [OK] license: ${SPDX}"
    ;;
esac

# --- Check 2: primary language is Python ---
# Permitted languages (hardcoded — extend this list when other languages are supported)
PERMITTED_LANGUAGES=("Python")

LANGUAGE=$(gh api "repos/${TARGET_REPO}" --jq '.language' 2>/dev/null || echo "null")

LANG_OK=0
for PERMITTED in "${PERMITTED_LANGUAGES[@]}"; do
  if [[ "${LANGUAGE}" == "${PERMITTED}" ]]; then
    LANG_OK=1
    break
  fi
done

if [[ "${LANG_OK}" -eq 0 ]]; then
  ERRORS+=("primary language '${LANGUAGE}' is not in the permitted set: ${PERMITTED_LANGUAGES[*]}")
else
  echo "  [OK] language: ${LANGUAGE}"
fi

# --- Check 3: vendored dependencies ---
VENDOR_COUNT=$(gh api "repos/${TARGET_REPO}/git/trees/HEAD?recursive=1" \
  --jq '[.tree[].path | select(test("^vendor/|^Godeps/|^third_party/|^_vendor/|^bower_components/|^node_modules/"))] | length' \
  2>/dev/null || echo "0")

if [[ "${VENDOR_COUNT}" -gt 0 ]]; then
  ERRORS+=("repo contains vendored dependencies (${VENDOR_COUNT} paths matched)")
else
  echo "  [OK] no vendored dependencies"
fi

echo

if [[ ${#ERRORS[@]} -eq 0 ]]; then
  echo "ELIGIBLE: ${TARGET_REPO} passes all checks. Proceed to step 2 (repo health check)."
else
  echo "INELIGIBLE: ${TARGET_REPO}"
  for ERR in "${ERRORS[@]}"; do
    echo "  - ${ERR}"
  done
  exit 1
fi
