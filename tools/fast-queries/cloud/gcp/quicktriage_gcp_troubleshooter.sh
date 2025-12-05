#!/usr/bin/env bash
# QuickTriage for GCP - Optimized version
# Only shows critical resource status

set -euo pipefail

PROJECT="${GCP_PROJECT:-${1:-}}"
CLUSTER="${GKE_CLUSTER:-${2:-}}"
REGION="${GKE_REGION:-${3:-us-central1}}"

# Get current project if not specified
if [ -z "$PROJECT" ]; then
    PROJECT=$(gcloud config get-value project 2>/dev/null || echo "")
fi

echo "=== GCP HEALTH CHECK: ${PROJECT:-no-project} ==="

if ! command -v gcloud >/dev/null 2>&1; then
    echo "❌ gcloud CLI not installed"
    exit 2
fi

if [ -z "$PROJECT" ]; then
    echo "❌ No GCP project configured"
    echo "  Run: gcloud config set project PROJECT_ID"
    exit 1
fi

# 1. GKE Clusters status (only if unhealthy)
echo -n "GKE Clusters: "
CLUSTERS=$(gcloud container clusters list --project="$PROJECT" --format="value(name,status)" 2>/dev/null || echo "")
if [ -z "$CLUSTERS" ]; then
    echo "⚠️  No clusters found"
else
    UNHEALTHY=$(echo "$CLUSTERS" | grep -v "RUNNING" || echo "")
    if [ -n "$UNHEALTHY" ]; then
        echo "❌ Issues detected"
        echo "$UNHEALTHY" | awk '{printf "  - %s: %s\n", $1, $2}'
    else
        CLUSTER_COUNT=$(echo "$CLUSTERS" | wc -l)
        echo "✅ $CLUSTER_COUNT cluster(s) running"
    fi
fi

# 2. Cloud SQL status (only if issues)
echo -n "Cloud SQL: "
SQL_INSTANCES=$(gcloud sql instances list --project="$PROJECT" --format="value(name,state)" 2>/dev/null || echo "")
if [ -z "$SQL_INSTANCES" ]; then
    echo "⚠️  No instances found"
else
    SQL_DOWN=$(echo "$SQL_INSTANCES" | grep -v "RUNNABLE" || echo "")
    if [ -n "$SQL_DOWN" ]; then
        echo "❌ Issues detected"
        echo "$SQL_DOWN" | awk '{printf "  - %s: %s\n", $1, $2}'
    else
        SQL_COUNT=$(echo "$SQL_INSTANCES" | wc -l)
        echo "✅ $SQL_COUNT instance(s) running"
    fi
fi

# 3. Recent errors (only critical)
echo -n "Recent errors: "
ERROR_COUNT=$(gcloud logging read "severity>=ERROR AND timestamp>=\"$(date -u -d '1 hour ago' '+%Y-%m-%dT%H:%M:%S')\"" \
    --limit=10 --project="$PROJECT" --format="value(textPayload)" 2>/dev/null | wc -l || echo "0")

if [ "$ERROR_COUNT" -gt 0 ]; then
    echo "⚠️  $ERROR_COUNT errors in last hour"
    # Show top 3 error sources
    gcloud logging read "severity>=ERROR AND timestamp>=\"$(date -u -d '1 hour ago' '+%Y-%m-%dT%H:%M:%S')\"" \
        --limit=10 --project="$PROJECT" --format="value(resource.labels.cluster_name,textPayload)" 2>/dev/null | \
        head -3 | sed 's/^/  - /' || true
else
    echo "✅ No recent errors"
fi

# 4. Quota warnings (only if near limits)
echo -n "Quota status: "
QUOTA_ISSUES=$(gcloud compute project-info describe --project="$PROJECT" --format="value(quotas[].usage,quotas[].limit)" 2>/dev/null | \
    awk '{if ($1/$2 > 0.8) print "High usage"}' | head -1 || echo "")

if [ -n "$QUOTA_ISSUES" ]; then
    echo "⚠️  Some quotas >80% used"
else
    echo "✅ All quotas healthy"
fi

# Exit code based on critical issues
[ -n "$UNHEALTHY" ] || [ -n "$SQL_DOWN" ] && exit 1 || exit 0