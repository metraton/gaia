#!/bin/bash
# QuickTriage script for DevOps Developer
# Description: Quick health check for application services and development environment

# Usage: quicktriage_devops_developer.sh [repo_path]

set -euo pipefail

REPO_PATH="${1:-.}"

echo "=========================================="
echo "DevOps Developer QuickTriage"
echo "=========================================="
echo "Repository: $REPO_PATH"
echo "=========================================="
echo ""

# Change to repo directory
cd "$REPO_PATH"

# Check for common configuration files
echo "### Configuration Files ###"
for file in package.json docker-compose.yml Dockerfile .env.example Makefile; do
    if [ -f "$file" ]; then
        echo "[OK] $file exists"
    else
        echo "[--] $file not found"
    fi
done
echo ""

# Check git status
echo "### Git Status ###"
if [ -d ".git" ]; then
    git status --short 2>/dev/null || echo "Git status failed"
    echo ""
    echo "Branch: $(git branch --show-current 2>/dev/null || echo 'unknown')"
else
    echo "Not a git repository"
fi
echo ""

# Check for node modules (if package.json exists)
if [ -f "package.json" ]; then
    echo "### Node.js Project ###"
    if [ -d "node_modules" ]; then
        echo "[OK] node_modules present"
    else
        echo "[WARN] node_modules missing - run npm install"
    fi
    
    # Check for common scripts
    echo ""
    echo "Available scripts:"
    if command -v jq &> /dev/null; then
        jq -r '.scripts | keys[]' package.json 2>/dev/null | head -10 || echo "Could not parse scripts"
    else
        grep -A20 '"scripts"' package.json 2>/dev/null | head -10 || echo "Could not read scripts"
    fi
    echo ""
fi

# Check Docker
echo "### Docker Status ###"
if command -v docker &> /dev/null; then
    echo "[OK] Docker available"
    docker info --format '{{.ContainersRunning}} containers running' 2>/dev/null || echo "Docker not accessible"
else
    echo "[--] Docker not installed"
fi
echo ""

echo "=========================================="
echo "QuickTriage Complete"
echo "=========================================="
