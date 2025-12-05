#!/usr/bin/env bash
# QuickTriage for GitOps - Optimized version
# Only shows problems, not everything

set -euo pipefail

NAMESPACE="${1:-tcm-non-prod}"

echo "=== HEALTH CHECK: $NAMESPACE ==="

# 1. Only problematic pods (not all pods)
PROBLEM_PODS=$(kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null | grep -v "Running\|Completed" || echo "")
if [ -n "$PROBLEM_PODS" ]; then
    echo "❌ PODS WITH ISSUES:"
    echo "$PROBLEM_PODS" | awk '{printf "  - %s: %s (restarts: %s)\n", $1, $3, $4}'
else
    echo "✅ All pods healthy"
fi

# 2. Only deployments with missing replicas
DEPLOY_ISSUES=$(kubectl get deploy -n "$NAMESPACE" --no-headers 2>/dev/null | awk '$2!=$3 {print $1, $2"/"$3}' || echo "")
if [ -n "$DEPLOY_ISSUES" ]; then
    echo "❌ DEPLOYMENTS NOT READY:"
    echo "$DEPLOY_ISSUES" | awk '{printf "  - %s: %s replicas\n", $1, $2}'
else
    echo "✅ All deployments ready"
fi

# 3. HelmRelease summary (1 line)
if command -v kubectl >/dev/null 2>&1 && kubectl api-resources | grep -q helmrelease 2>/dev/null; then
    HR_COUNT=$(kubectl get helmrelease -n "$NAMESPACE" --no-headers 2>/dev/null | wc -l || echo "0")
    HR_FAILED=$(kubectl get helmrelease -n "$NAMESPACE" --no-headers 2>/dev/null | grep -c False || echo "0")
    if [ "$HR_FAILED" -gt 0 ]; then
        echo "❌ HelmReleases: $HR_FAILED/$HR_COUNT failed"
    elif [ "$HR_COUNT" -gt 0 ]; then
        echo "✅ HelmReleases: $HR_COUNT healthy"
    fi
fi

# 4. Recent warnings only (last 5)
WARNINGS=$(kubectl get events -n "$NAMESPACE" --field-selector type=Warning --no-headers 2>/dev/null | tail -5 || echo "")
if [ -n "$WARNINGS" ]; then
    echo "⚠️  Recent warnings:"
    echo "$WARNINGS" | awk '{print "  - " $5 ": " substr($0, index($0,$6))}'
fi

# Exit code based on issues
[ -n "$PROBLEM_PODS" ] || [ -n "$DEPLOY_ISSUES" ] || [ "$HR_FAILED" -gt 0 ] && exit 1 || exit 0