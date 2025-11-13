# Gaia-Ops Installation Guide

## Quick Start

```bash
# 1. Run interactive setup (recommended)
npx @jaguilar87/gaia-ops init

# 2. Or use non-interactive mode
npx @jaguilar87/gaia-ops init --non-interactive \
  --gitops ./gitops \
  --terraform ./terraform \
  --app-services ./app-services \
  --project-id my-project \
  --cluster my-cluster
```

## Avoiding Multiple Claude Code Installations

**⚠️ Important:** This project automatically detects existing Claude Code installations and **does NOT reinstall them**. This prevents the duplicate installation warnings.

### If You Already Have Multiple Installations

#### Option 1: Automated Cleanup
```bash
# Run the cleanup script
npx @jaguilar87/gaia-ops cleanup
```

#### Option 2: Manual Cleanup
```bash
# Remove the npm global installation
npm -g uninstall @anthropic-ai/claude-code

# Verify only one installation remains
which claude
claude --version
```

### Verification
```bash
# Should show only ONE installation location
which claude
# Example output: /home/jaguilar/.local/bin/claude

# Should NOT show multiple installations
npm list -g @anthropic-ai/claude-code 2>/dev/null
```

## Installation Behavior

| Scenario | Behavior |
|----------|----------|
| Claude Code installed (native) | ✅ Skips reinstall, uses existing |
| Claude Code not installed + `--skip-claude-install` | ✅ Skips install, proceeds |
| Claude Code not installed + no skip flag | ✅ Installs once (native/recommended) |
| npm global + native installed | ⚠️ Runs cleanup to remove npm global |

## CLI Options

```
gaia-init [options]

Options:
  --non-interactive          Skip prompts, use provided values or defaults
  --gitops <path>           GitOps directory path
  --terraform <path>        Terraform directory path
  --app-services <path>     App services directory path
  --project-id <id>         GCP Project ID
  --region <region>         Primary region (default: us-central1)
  --cluster <name>          Cluster name
  --skip-claude-install     Skip Claude Code installation
```

## Environment Variables

```bash
export CLAUDE_GITOPS_DIR=./gitops
export CLAUDE_TERRAFORM_DIR=./terraform
export CLAUDE_APP_SERVICES_DIR=./app-services
export CLAUDE_PROJECT_ID=my-project
export CLAUDE_REGION=us-central1
export CLAUDE_CLUSTER_NAME=my-cluster

npx @jaguilar87/gaia-ops init --non-interactive
```

## Post-Installation

1. ✅ Review generated `.claude/` directory
2. ✅ Review `CLAUDE.md` - adjust paths if needed
3. ✅ Update `.claude/project-context/project-context.json`
4. ✅ Start Claude Code: `claude`

## Package Updates

⚠️ **IMPORTANT:** When you update the `@jaguilar87/gaia-ops` package, the following files will be **OVERWRITTEN** from templates:

- `CLAUDE.md` - All customizations will be lost
- `.claude/settings.json` - All customizations will be lost

### Update Behavior

```bash
# When you run npm install or update
npm install @jaguilar87/gaia-ops@latest

# The postinstall hook automatically:
# 1. Shows warning about files being overwritten
# 2. Regenerates CLAUDE.md from template
# 3. Regenerates .claude/settings.json from template
# 4. Does NOT touch other files in .claude/ (symlinked)
```

### Best Practices

| File | Update Strategy |
|------|----------------|
| `CLAUDE.md` | ⚠️ Will be overwritten - reconfigure after updates |
| `.claude/settings.json` | ⚠️ Will be overwritten - backup if you customize |
| `.claude/project-context/` | ✅ Safe - not touched by updates |
| `.claude/logs/` | ✅ Safe - not touched by updates |
| Other `.claude/` files | ✅ Auto-updated via symlinks |

### Backup Workflow (Optional)

If you heavily customize `CLAUDE.md` or `settings.json`:

```bash
# Before update - backup customizations
cp CLAUDE.md CLAUDE.md.backup
cp .claude/settings.json .claude/settings.json.backup

# Update package
npm install @jaguilar87/gaia-ops@latest

# After update - restore customizations manually
# (compare and merge changes as needed)
```

## Troubleshooting

### Multiple Claude Code Installations
```bash
# Diagnose
npm list -g @anthropic-ai/claude-code

# Fix
npm -g uninstall @anthropic-ai/claude-code
npx @jaguilar87/gaia-ops cleanup
```

### Claude Code Not Found After Installation
```bash
# Verify installation
claude --version

# If not found, install manually
npm install -g @anthropic-ai/claude-code
```

### Permission Denied on npm global install
```bash
# Option 1: Use sudo (not recommended)
sudo npm install -g @anthropic-ai/claude-code --unsafe-perm

# Option 2: Fix npm permissions (recommended)
mkdir ~/.npm-global
npm config set prefix '~/.npm-global'
export PATH=~/.npm-global/bin:$PATH
```

## Design Principles

✅ **Minimal:** Only creates what's needed, no duplicates
✅ **Adaptive:** Auto-detects existing installations
✅ **Non-invasive:** Works from any directory
✅ **Safe:** Validates paths and skips reinstalls
✅ **Clear:** Explicit feedback on each step

## Support

- Issues: https://github.com/metraton/gaia-ops/issues
- Email: jaguilar1897@gmail.com
