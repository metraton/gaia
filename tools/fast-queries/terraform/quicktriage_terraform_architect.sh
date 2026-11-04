#!/usr/bin/env bash
# Prints format, validation and plan verdicts for one directory, with terragrunt
# when USE_TERRAGRUNT=true or the directory has a terragrunt.hcl. Exits 1 only on a validation Error.

set -euo pipefail

TARGET_DIR="${1:-.}"
USE_TERRAGRUNT="${USE_TERRAGRUNT:-false}"

if [ "$USE_TERRAGRUNT" != "true" ] && [ -f "$TARGET_DIR/terragrunt.hcl" ]; then
    USE_TERRAGRUNT="true"
fi

TOOL="terraform"
if [ "$USE_TERRAGRUNT" == "true" ]; then
    TOOL="terragrunt"
fi

echo "=== TERRAFORM CHECK: $TARGET_DIR ($TOOL) ==="

if ! command -v "$TOOL" >/dev/null 2>&1; then
    echo "❌ $TOOL not installed"
    exit 2
fi

cd "$TARGET_DIR" || exit 2

if $TOOL fmt -check -diff=false > /dev/null 2>&1; then
    echo "✅ Format OK"
else
    echo "❌ Format issues (run: $TOOL fmt)"
fi

if [ ! -d ".terraform" ] && [ "$TOOL" == "terraform" ]; then
    terraform init -backend=false -upgrade=false > /dev/null 2>&1 || true
fi

VALIDATION_OUTPUT=$($TOOL validate 2>&1 || true)
if echo "$VALIDATION_OUTPUT" | grep -q "Success\|configuration is valid"; then
    echo "✅ Valid configuration"
elif [ -z "$VALIDATION_OUTPUT" ]; then
    echo "✅ Valid configuration"
else
    echo "❌ Validation failed:"
    echo "$VALIDATION_OUTPUT" | grep -E "Error:|Warning:" | head -3 | sed 's/^/  /'
fi

if [ "$USE_TERRAGRUNT" == "true" ]; then
    PLAN_OUTPUT=$(terragrunt plan -lock=false 2>&1 | tail -20 || true)
else
    set +e
    terraform plan -lock=false -refresh=false -detailed-exitcode -out=/dev/null > /tmp/tf-plan-$$.txt 2>&1
    PLAN_EXIT=$?
    set -e

    if [ $PLAN_EXIT -eq 0 ]; then
        echo "✅ No changes needed"
    elif [ $PLAN_EXIT -eq 2 ]; then
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

[ -n "$(echo "$VALIDATION_OUTPUT" | grep -E "Error:")" ] && exit 1 || exit 0