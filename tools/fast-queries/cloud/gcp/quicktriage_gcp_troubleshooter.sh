#!/usr/bin/env bash
# QuickTriage script for gcp-troubleshooter
# Provides a lightweight health snapshot for GKE clusters and key managed services.

set -euo pipefail

PROJECT="${GCP_PROJECT:-${1:-}}"
CLUSTER="${GKE_CLUSTER:-${2:-}}"
REGION="${GKE_REGION:-${3:-us-central1}}"
SQL_INSTANCE="${CLOUD_SQL_INSTANCE:-${4:-}}"

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

info "Starting GCP quick triage (project=${PROJECT:-unset}, cluster=${CLUSTER:-unset})"

if [[ -n "$PROJECT" ]]; then
  gcloud config set project "$PROJECT" >/dev/null 2>&1 || true
fi

if [[ -n "$CLUSTER" ]]; then
  run_cmd "gcloud container clusters describe ${CLUSTER}" \
    gcloud container clusters describe "$CLUSTER" --region "$REGION" --format="table(name,status,endpoint,releaseChannel.releaseChannel)"
fi

run_cmd "gcloud container clusters list (summary)" \
  gcloud container clusters list --format="table(name,location,status,nodePools[0].status)"

if [[ -n "$SQL_INSTANCE" ]]; then
  run_cmd "gcloud sql instances describe ${SQL_INSTANCE}" \
    gcloud sql instances describe "$SQL_INSTANCE" --format="table(name,state,backendType,availabilityType,ipAddresses.ipAddress)"
fi

run_cmd "gcloud logging read (recent errors)" \
  gcloud logging read 'severity>=ERROR' --limit=5 --format="table(timestamp, resource.labels.cluster_name, textPayload)"

info "Quick triage completed. Consider VPC connectivity, IAM bindings, or workload identity if issues persist."
