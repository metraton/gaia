#!/usr/bin/env bash
# Fast-Queries Runner - Simplified version
# Only runs the 3 essential triages

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELECTED="${1:-all}"

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

run_script() {
    local script="$1"
    local name="$2"

    echo -e "\n${YELLOW}Running $name...${NC}"

    if [ ! -f "$script" ]; then
        echo -e "${RED}✗ Script not found: $script${NC}"
        return 1
    fi

    if bash "$script"; then
        echo -e "${GREEN}✓ $name completed${NC}"
        return 0
    else
        echo -e "${RED}✗ $name found issues${NC}"
        return 1
    fi
}

echo "=== FAST HEALTH CHECK ==="

case "$SELECTED" in
    all)
        run_script "$SCRIPT_DIR/gitops/quicktriage_gitops_operator.sh" "GitOps"
        run_script "$SCRIPT_DIR/terraform/quicktriage_terraform_architect.sh" "Terraform"
        run_script "$SCRIPT_DIR/cloud/gcp/quicktriage_gcp_troubleshooter.sh" "GCP"
        ;;
    gitops|k8s|kubernetes)
        run_script "$SCRIPT_DIR/gitops/quicktriage_gitops_operator.sh" "GitOps"
        ;;
    terraform|tf)
        run_script "$SCRIPT_DIR/terraform/quicktriage_terraform_architect.sh" "Terraform"
        ;;
    gcp|cloud)
        run_script "$SCRIPT_DIR/cloud/gcp/quicktriage_gcp_troubleshooter.sh" "GCP"
        ;;
    *)
        echo "Usage: $0 [all|gitops|terraform|gcp]"
        exit 1
        ;;
esac

echo -e "\n${GREEN}Health check complete${NC}"