#!/usr/bin/env bash
# QuickTriage for Terraform - Optimized version
# Only shows validation results, not full output

set -euo pipefail

TARGET_DIR="${1:-.}"
USE_TERRAGRUNT="${USE_TERRAGRUNT:-false}"

# Detect if should use terragrunt based on files present
if [ "$USE_TERRAGRUNT" != "true" ] && [ -f "$TARGET_DIR/terragrunt.hcl" ]; then
    USE_TERRAGRUNT="true"
fi

TOOL="terraform"
if [ "$USE_TERRAGRUNT" == "true" ]; then
    TOOL="terragrunt"
fi

echo "=== TERRAFORM CHECK: $TARGET_DIR ($TOOL) ==="

# Check if tool exists
if ! command -v "$TOOL" >/dev/null 2>&1; then
    echo "❌ $TOOL not installed"
    exit 2
fi

cd "$TARGET_DIR" || exit 2

# 1. Format check (only show result, not diff)
if $TOOL fmt -check -diff=false > /dev/null 2>&1; then
    echo "✅ Format OK"
else
    echo "❌ Format issues (run: $TOOL fmt)"
fi

# 2. Init if needed (silent)
if [ ! -d ".terraform" ] && [ "$TOOL" == "terraform" ]; then
    terraform init -backend=false -upgrade=false > /dev/null 2>&1 || true
fi

# 3. Validation (only show if fails)
VALIDATION_OUTPUT=$($TOOL validate 2>&1 || true)
if echo "$VALIDATION_OUTPUT" | grep -q "Success\|configuration is valid"; then
    echo "✅ Valid configuration"
elif [ -z "$VALIDATION_OUTPUT" ]; then
    echo "✅ Valid configuration"
else
    echo "❌ Validation failed:"
    echo "$VALIDATION_OUTPUT" | grep -E "Error:|Warning:" | head -3 | sed 's/^/  /'
fi

# 4. Plan summary (only count changes, don't show full plan)
if [ "$USE_TERRAGRUNT" == "true" ]; then
    # Terragrunt doesn't support -detailed-exitcode well, just check for drift
    PLAN_OUTPUT=$(terragrunt plan -lock=false 2>&1 | tail -20 || true)
else
    # Terraform with detailed exit code
    set +e
    terraform plan -lock=false -refresh=false -detailed-exitcode -out=/dev/null > /tmp/tf-plan-$$.txt 2>&1
    PLAN_EXIT=$?
    set -e

    if [ $PLAN_EXIT -eq 0 ]; then
        echo "✅ No changes needed"
    elif [ $PLAN_EXIT -eq 2 ]; then
        # Changes detected, count them
        PLAN_OUTPUT=$(cat /tmp/tf-plan-$$.txt)
        ADD=$(echo "$PLAN_OUTPUT" | grep -c "will be created" || echo "0")
        UPDATE=$(echo "$PLAN_OUTPUT" | grep -c "will be updated\|will be modified" || echo "0")
        DELETE=$(echo "$PLAN_OUTPUT" | grep -c "will be destroyed" || echo "0")
        echo "⚠️  Changes detected: +$ADD ~$UPDATE -$DELETE"
    else
        echo "❌ Plan failed"
    fi
    rm -f /tmp/tf-plan-$$.txt
fi

# Exit code: 0 if all OK, 1 if issues found
[ -n "$(echo "$VALIDATION_OUTPUT" | grep -E "Error:")" ] && exit 1 || exit 0