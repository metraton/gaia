#!/usr/bin/env bash
# Common functions for Spec-Kit scripts - Simplified Version 2.0
# All commands now receive explicit arguments: <speckit-root> <feature-name>

# Get repository root
get_repo_root() {
    if git rev-parse --show-toplevel >/dev/null 2>&1; then
        git rev-parse --show-toplevel
    else
        # Fall back to script location for non-git repos
        local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        (cd "$script_dir/../../.." && pwd)
    fi
}

# Derive all paths from explicit arguments
# Usage: get_feature_paths <speckit-root> <feature-name>
# Returns: Environment variables for all paths
get_feature_paths() {
    local speckit_root_arg="${1:-}"
    local feature_name_arg="${2:-}"

    # Validate arguments
    if [[ -z "$speckit_root_arg" ]]; then
        echo "ERROR: speckit-root argument is required" >&2
        echo "Usage: <command> <speckit-root> <feature-name>" >&2
        echo "Example: setup-plan.sh spec-kit-tcm-plan 004-project-guidance-deployment" >&2
        return 1
    fi

    if [[ -z "$feature_name_arg" ]]; then
        echo "ERROR: feature-name argument is required" >&2
        echo "Usage: <command> <speckit-root> <feature-name>" >&2
        echo "Example: setup-plan.sh spec-kit-tcm-plan 004-project-guidance-deployment" >&2
        return 1
    fi

    local repo_root=$(get_repo_root)

    # Resolve speckit_root to absolute path
    local speckit_root
    if [[ "$speckit_root_arg" = /* ]]; then
        # Already absolute path
        speckit_root="$speckit_root_arg"
    else
        # Relative path - resolve from repo root
        speckit_root="$repo_root/$speckit_root_arg"
    fi

    # Extract feature name (if path was provided instead of name)
    local feature_name
    if [[ "$feature_name_arg" == *"/"* ]]; then
        # Extract basename from path
        feature_name=$(basename "$feature_name_arg")
    else
        feature_name="$feature_name_arg"
    fi

    # Clean up feature name
    feature_name=$(echo "$feature_name" | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

    # Derive all paths from these two arguments
    local specs_dir="$speckit_root/specs"
    local feature_dir="$specs_dir/$feature_name"
    local constitution_path="$speckit_root/governance.md"
    local templates_dir="$repo_root/.claude/speckit/templates"
    local scripts_dir="$repo_root/.claude/speckit/scripts"

    # Output all paths
    cat <<EOF
REPO_ROOT='$repo_root'
SPECKIT_ROOT='$speckit_root'
SPECS_DIR='$specs_dir'
FEATURE_NAME='$feature_name'
FEATURE_DIR='$feature_dir'
FEATURE_SPEC='$feature_dir/spec.md'
IMPL_PLAN='$feature_dir/plan.md'
TASKS='$feature_dir/tasks.md'
RESEARCH='$feature_dir/research.md'
DATA_MODEL='$feature_dir/data-model.md'
QUICKSTART='$feature_dir/quickstart.md'
CONTRACTS_DIR='$feature_dir/contracts'
CONSTITUTION_PATH='$constitution_path'
TEMPLATES_DIR='$templates_dir'
SCRIPTS_DIR='$scripts_dir'
EOF
}

# Utility functions for validation
check_file() {
    [[ -f "$1" ]] && echo "  ✓ $2" || echo "  ✗ $2"
}

check_dir() {
    [[ -d "$1" && -n $(ls -A "$1" 2>/dev/null) ]] && echo "  ✓ $2" || echo "  ✗ $2"
}

# Validate speckit root directory exists
validate_speckit_root() {
    local speckit_root="$1"

    if [[ ! -d "$speckit_root" ]]; then
        echo "ERROR: Spec-Kit root directory not found: $speckit_root" >&2
        echo "" >&2
        echo "Create it first: mkdir -p $speckit_root" >&2
        return 1
    fi

    # Optionally warn if governance document doesn't exist
    if [[ ! -f "$speckit_root/governance.md" ]]; then
        echo "WARNING: governance.md not found at $speckit_root/governance.md" >&2
        echo "Consider creating it for project governance." >&2
    fi

    return 0
}

# Create specs directory if it doesn't exist
ensure_specs_dir() {
    local specs_dir="$1"

    if [[ ! -d "$specs_dir" ]]; then
        mkdir -p "$specs_dir"
        echo "Created specs directory: $specs_dir"
    fi
}
