#!/usr/bin/env bash
# QuickTriage script for terraform-architect
# Performs fast validation checks on Terraform/Terragrunt directories.

set -euo pipefail

TARGET_DIR="${1:-.}"
USE_TERRAGRUNT="${USE_TERRAGRUNT:-false}"

info() {
  printf '[quicktriage] %s\n' "$*"
}

run_cmd() {
  local description="$1"
  shift

  if ! command -v "$1" >/dev/null 2>&1; then
    info "Skipping ${description} (command $1 not available)"
    return
  fi

  info "$description"
  (cd "$TARGET_DIR" && "$@") || info "Command failed: $*"
}

info "Starting Terraform quick triage (dir=${TARGET_DIR}, terragrunt=${USE_TERRAGRUNT})"

if [[ "${USE_TERRAGRUNT}" == "true" ]]; then
  run_cmd "terragrunt fmt -check" terragrunt fmt -check
  run_cmd "terragrunt validate" terragrunt validate
  run_cmd "terragrunt plan (detailed exit code)" terragrunt plan -lock=false -detailed-exitcode || true
else
  run_cmd "terraform fmt -check" terraform fmt -check
  run_cmd "terraform init -backend=false" terraform init -backend=false
  run_cmd "terraform validate" terraform validate
  run_cmd "terraform plan (detailed exit code)" terraform plan -lock=false -refresh=false -detailed-exitcode || true
fi

info "Quick triage completed. Exit code 1 on plan indicates drift; review the plan output if printed."
