#!/usr/bin/env bash
# QuickTriage for GCP - Optimized version
# Only shows critical resource status

set -euo pipefail

PROJECT="${GCP_PROJECT:-${1:-}}"
CLUSTER="${GKE_CLUSTER:-${2:-}}"
REGION="${GKE_REGION:-${3:-us-central1}}"

if ! command -v gcloud >/dev/null 2>&1; then
    echo "❌ gcloud CLI not installed"
    exit 2
fi

GCLOUD_ERROR=""
# Capture gcloud stdout into the named variable, keeping stderr out of the
# captured value: gcloud prints configuration chatter and WARNINGs on stderr,
# and folding them into the data stream makes a healthy project report issues.
gcloud_capture() {
    local target_var="$1"
    shift
    local stderr_file output
    stderr_file=$(mktemp)
    if output=$(gcloud "$@" 2>"$stderr_file"); then
        rm -f "$stderr_file"
        printf -v "$target_var" '%s' "$output"
        GCLOUD_ERROR=""
        return 0
    fi
    GCLOUD_ERROR=$(head -n 1 "$stderr_file" || true)
    rm -f "$stderr_file"
    [ -n "$GCLOUD_ERROR" ] || GCLOUD_ERROR="${output%%$'\n'*}"
    [ -n "$GCLOUD_ERROR" ] || GCLOUD_ERROR="gcloud exited without an error message"
    return 1
}

# Get current project if not specified. A failed identity/config lookup is not
# equivalent to an empty configuration: surface it as an operational failure.
if [ -z "$PROJECT" ]; then
    if ! gcloud_capture PROJECT config get-value project; then
        echo "❌ Unable to read the configured GCP project"
        echo "  - $GCLOUD_ERROR"
        exit 2
    fi
fi

echo "=== GCP HEALTH CHECK: ${PROJECT:-no-project} ==="

if [ -z "$PROJECT" ]; then
    echo "❌ No GCP project configured"
    echo "  Run: gcloud config set project PROJECT_ID"
    exit 1
fi

QUERY_FAILURE=0
UNHEALTHY=""
SQL_DOWN=""

# 1. GKE Clusters status (only if unhealthy)
echo -n "GKE Clusters: "
if ! gcloud_capture CLUSTERS container clusters list \
    --project="$PROJECT" --format="value(name,status)"; then
    echo "❌ Query failed"
    echo "  - $GCLOUD_ERROR"
    QUERY_FAILURE=1
elif [ -z "$CLUSTERS" ]; then
    echo "⚠️  No clusters found"
else
    UNHEALTHY=$(printf '%s\n' "$CLUSTERS" | grep -v "RUNNING" || true)
    if [ -n "$UNHEALTHY" ]; then
        echo "❌ Issues detected"
        printf '%s\n' "$UNHEALTHY" | awk '{printf "  - %s: %s\n", $1, $2}'
    else
        CLUSTER_COUNT=$(printf '%s\n' "$CLUSTERS" | wc -l | tr -d '[:space:]')
        echo "✅ $CLUSTER_COUNT cluster(s) running"
    fi
fi

# 2. Cloud SQL status (only if issues)
echo -n "Cloud SQL: "
if ! gcloud_capture SQL_INSTANCES sql instances list \
    --project="$PROJECT" --format="value(name,state)"; then
    echo "❌ Query failed"
    echo "  - $GCLOUD_ERROR"
    QUERY_FAILURE=1
elif [ -z "$SQL_INSTANCES" ]; then
    echo "⚠️  No instances found"
else
    SQL_DOWN=$(printf '%s\n' "$SQL_INSTANCES" | grep -v "RUNNABLE" || true)
    if [ -n "$SQL_DOWN" ]; then
        echo "❌ Issues detected"
        printf '%s\n' "$SQL_DOWN" | awk '{printf "  - %s: %s\n", $1, $2}'
    else
        SQL_COUNT=$(printf '%s\n' "$SQL_INSTANCES" | wc -l | tr -d '[:space:]')
        echo "✅ $SQL_COUNT instance(s) running"
    fi
fi

# 3. Recent errors (only critical)
echo -n "Recent errors: "
LOG_FILTER="severity>=ERROR AND timestamp>=\"$(date -u -d '1 hour ago' '+%Y-%m-%dT%H:%M:%S')\""
if ! gcloud_capture RECENT_ERRORS logging read "$LOG_FILTER" \
    --limit=10 --project="$PROJECT" \
    --format="value(resource.labels.cluster_name,textPayload)"; then
    echo "❌ Query failed"
    echo "  - $GCLOUD_ERROR"
    QUERY_FAILURE=1
elif [ -n "$RECENT_ERRORS" ]; then
    ERROR_COUNT=$(printf '%s\n' "$RECENT_ERRORS" | sed '/^[[:space:]]*$/d' | wc -l | tr -d '[:space:]')
    echo "⚠️  $ERROR_COUNT errors in last hour"
    # `|| true`: head -3 closes the pipe early; on a large payload printf dies
    # with SIGPIPE (141) and pipefail would abort the whole triage. The
    # pipeline's exit status is never consulted, only its output.
    printf '%s\n' "$RECENT_ERRORS" | head -3 | sed 's/^/  - /' || true
else
    echo "✅ No recent errors"
fi

# 4. Quota warnings (only if near limits)
echo -n "Quota status: "
if ! gcloud_capture QUOTAS compute project-info describe \
    --project="$PROJECT" --format="value(quotas[].usage,quotas[].limit)"; then
    echo "❌ Query failed"
    echo "  - $GCLOUD_ERROR"
    QUERY_FAILURE=1
elif printf '%s\n' "$QUOTAS" | awk 'NF >= 2 && $2 > 0 && $1/$2 > 0.8 {found=1} END {exit !found}'; then
    echo "⚠️  Some quotas >80% used"
else
    echo "✅ All quotas healthy"
fi

# Operational query failures take precedence over a health verdict. A caller
# must never interpret missing permissions/authentication as a healthy project.
if [ "$QUERY_FAILURE" -ne 0 ]; then
    exit 2
fi
if [ -n "$UNHEALTHY" ] || [ -n "$SQL_DOWN" ]; then
    exit 1
fi
exit 0
