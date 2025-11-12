#!/usr/bin/env bash
# QuickTriage script for aws-troubleshooter
# Provides a minimal health snapshot for AWS EKS clusters and supporting services.

set -euo pipefail

REGION="${AWS_REGION:-${1:-us-east-1}}"
CLUSTER="${EKS_CLUSTER:-${2:-}}"
VPC_ID="${VPC_ID:-${3:-}}"

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

info "Starting AWS quick triage (region=${REGION}, cluster=${CLUSTER:-unset})"

AWS_ARGS=(--region "$REGION")

if [[ -n "$CLUSTER" ]]; then
  run_cmd "aws eks describe-cluster ${CLUSTER}" aws eks describe-cluster "${AWS_ARGS[@]}" --name "$CLUSTER" --query "cluster.{name:name,status:status,endpoint:endpoint,version:version}" --output table
  run_cmd "kubectl get nodes (short)" kubectl get nodes -o wide
fi

run_cmd "aws elbv2 describe-target-health (summary)" aws elbv2 describe-target-health "${AWS_ARGS[@]}" --target-group-arn "${TARGET_GROUP_ARN:-}" || info "Set TARGET_GROUP_ARN to include ALB status in triage."

run_cmd "aws cloudwatch describe-alarms (ALARM state)" aws cloudwatch describe-alarms "${AWS_ARGS[@]}" --state-value ALARM --max-items 10 --query "MetricAlarms[*].{Name:AlarmName,State:StateValue}" --output table

if [[ -n "$VPC_ID" ]]; then
  run_cmd "aws ec2 describe-vpc-endpoints ${VPC_ID}" aws ec2 describe-vpc-endpoints "${AWS_ARGS[@]}" --filters "Name=vpc-id,Values=${VPC_ID}" --query "VpcEndpoints[*].{ServiceName:ServiceName,State:State}" --output table
fi

info "Quick triage completed. Investigate failing components by describing pods/services or reviewing IAM/VPC configuration as next steps."
