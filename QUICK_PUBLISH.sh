#!/bin/bash
set -e

echo "🚀 Publicando Gaia-Ops 2.6.2..."
echo ""

cd /home/jaguilar/aaxis/vtr/repositories/gaia-ops

# Commit
echo "→ Git commit..."
git add -A
git commit -m "feat: add absolute paths support and project-context-repo flag

- Add normalizePath() function for absolute/relative path handling
- Add --project-context-repo flag for non-interactive mode
- Add CLAUDE_PROJECT_CONTEXT_REPO env var
- Bump version to 2.6.2"

echo "✓ Commit done"
echo ""

# Publish
echo "→ Publishing to npm..."
npm publish

echo ""
echo "✓ Version 2.6.2 published!"
echo ""
echo "Waiting 5 seconds for npm to propagate..."
sleep 5

echo ""
echo "✅ Ready to test!"

