#!/usr/bin/env bash
# Fast-Queries Central Runner
# Executes selected or all agent triages with unified output

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELECTED_AGENT="${1:-all}"
VERBOSE="${VERBOSE:-false}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_header() {
  printf "\n${BLUE}=== %s ===${NC}\n" "$1"
}

print_result() {
  local status=$1
  local msg=$2
  if [[ $status -eq 0 ]]; then
    printf "${GREEN}✓${NC} %s\n" "$msg"
  else
    printf "${RED}✗${NC} %s (exit code: $status)\n" "$msg"
  fi
}

run_triage() {
  local script=$1
  local desc=$2

  print_header "$desc"

  if [[ ! -f "$script" ]]; then
    printf "${YELLOW}⊘${NC} Script not found: $script\n"
    return 1
  fi

  if bash "$script"; then
    print_result 0 "$desc completed successfully"
    return 0
  else
    local exit_code=$?
    print_result "$exit_code" "$desc encountered issues"
    return "$exit_code"
  fi
}

# Count successes/failures
TOTAL=0
PASSED=0
FAILED=0

# Terraform
if [[ "$SELECTED_AGENT" == "all" ]] || [[ "$SELECTED_AGENT" == "terraform" ]]; then
  TOTAL=$((TOTAL + 1))
  if run_triage \
    "$SCRIPT_DIR/terraform/quicktriage_terraform_architect.sh" \
    "Terraform Architecture Triage"; then
    PASSED=$((PASSED + 1))
  else
    FAILED=$((FAILED + 1))
  fi
fi

# GitOps
if [[ "$SELECTED_AGENT" == "all" ]] || [[ "$SELECTED_AGENT" == "gitops" ]]; then
  TOTAL=$((TOTAL + 1))
  if run_triage \
    "$SCRIPT_DIR/gitops/quicktriage_gitops_operator.sh" \
    "GitOps Operator Triage"; then
    PASSED=$((PASSED + 1))
  else
    FAILED=$((FAILED + 1))
  fi
fi

# GCP
if [[ "$SELECTED_AGENT" == "all" ]] || [[ "$SELECTED_AGENT" == "gcp" ]]; then
  TOTAL=$((TOTAL + 1))
  if run_triage \
    "$SCRIPT_DIR/cloud/gcp/quicktriage_gcp_troubleshooter.sh" \
    "GCP Troubleshooter Triage"; then
    PASSED=$((PASSED + 1))
  else
    FAILED=$((FAILED + 1))
  fi
fi

# AWS
if [[ "$SELECTED_AGENT" == "all" ]] || [[ "$SELECTED_AGENT" == "aws" ]]; then
  TOTAL=$((TOTAL + 1))
  if run_triage \
    "$SCRIPT_DIR/cloud/aws/quicktriage_aws_troubleshooter.sh" \
    "AWS Troubleshooter Triage"; then
    PASSED=$((PASSED + 1))
  else
    FAILED=$((FAILED + 1))
  fi
fi

# DevOps/AppServices
if [[ "$SELECTED_AGENT" == "all" ]] || [[ "$SELECTED_AGENT" == "devops" ]]; then
  TOTAL=$((TOTAL + 1))
  if run_triage \
    "$SCRIPT_DIR/appservices/quicktriage_devops_developer.sh" \
    "DevOps Developer Triage"; then
    PASSED=$((PASSED + 1))
  else
    FAILED=$((FAILED + 1))
  fi
fi

# Summary
print_header "Summary"
printf "Total: %d | Passed: ${GREEN}%d${NC} | Failed: ${RED}%d${NC}\n" \
  "$TOTAL" "$PASSED" "$FAILED"

if [[ $FAILED -gt 0 ]]; then
  exit 1
fi
