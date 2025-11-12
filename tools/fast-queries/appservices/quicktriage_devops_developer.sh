#!/usr/bin/env bash
# QuickTriage script for devops-developer
# Checks repository hygiene, linting, and test discoverability quickly.

set -euo pipefail

WORKDIR="${1:-.}"
LINT_CMD="${LINT_CMD:-npm run lint -- --max-warnings=0}"
TEST_DISCOVERY_CMD="${TEST_DISCOVERY_CMD:-npm run test -- --watchAll=false --listTests}"

info() {
  printf '[quicktriage] %s\n' "$*"
}

run_in_repo() {
  local description="$1"
  shift

  info "$description"
  (cd "$WORKDIR" && eval "$*") || info "Command failed: $*"
}

info "Starting devops quick triage (workdir=${WORKDIR})"

run_in_repo "git status --short" "git status --short"

if command -v npm >/dev/null 2>&1 || command -v pnpm >/dev/null 2>&1; then
  run_in_repo "Lint check" "$LINT_CMD"
  run_in_repo "Test discovery" "$TEST_DISCOVERY_CMD"
else
  info "Skipping lint/test (npm/pnpm not available)"
fi

if [ -f "${WORKDIR}/package.json" ]; then
  run_in_repo "npm audit --production (summary)" "npm audit --production --json | jq '.metadata.vulnerabilities' || npm audit --production"
fi

info "Quick triage completed. Use full test runs or profiling if deeper analysis is required."
