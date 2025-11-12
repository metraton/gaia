#!/usr/bin/env bash
# QuickTriage script for gitops-operator
# Provides a fast snapshot of workload health inside a Kubernetes cluster.

set -euo pipefail

NAMESPACE="${1:-tcm-non-prod}"
LABEL_SELECTOR="${2:-}"

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
  "$@" || info "Command failed: $*"
}

info "Starting gitops quick triage (namespace=${NAMESPACE:-all}, selector='${LABEL_SELECTOR}')"

KUBECTL_ARGS=(-o wide)
if [[ -n "$NAMESPACE" ]]; then
  KUBECTL_ARGS=(-n "$NAMESPACE" "${KUBECTL_ARGS[@]}")
fi
if [[ -n "$LABEL_SELECTOR" ]]; then
  KUBECTL_ARGS+=(-l "$LABEL_SELECTOR")
fi

run_cmd "kubectl get pods" kubectl get pods "${KUBECTL_ARGS[@]}"

if [[ -n "$NAMESPACE" ]]; then
  run_cmd "kubectl get deploy" kubectl get deploy -n "$NAMESPACE"
  run_cmd "kubectl get helmrelease" kubectl get helmrelease -n "$NAMESPACE"
fi

run_cmd "flux get kustomizations" flux get kustomizations
run_cmd "flux get helmreleases" flux get helmreleases -A

info "Quick triage completed. Recommended next steps: describe failing pods or inspect logs if issues were detected."
