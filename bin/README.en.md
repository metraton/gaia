# Gaia-Ops Utility Scripts

**[🇪🇸 Versión en español](README.md)**

This directory contains utility scripts to install, update and manage the gaia-ops package. They're the command-line tools that facilitate the system lifecycle.

## 🎯 Purpose

The bin/ scripts automate common package management tasks. They provide a friendly interface for operations that would otherwise require complex manual steps.

**Problem it solves:** Installing and configuring an agent system can be complex. These scripts automate detection, installation and configuration, reducing errors and saving time.

## 🔄 How It Works

### Architecture Flow

```
User executes bin/script
        ↓
[Script] detects current state
        ↓
    Executes actions
    ┌────────┴────────┐
    ↓                 ↓
[Installation]    [Cleanup]
    ↓                 ↓
Configure symlinks  Remove files
    ↓                 ↓
Validate result
```

### Real Example Flow

```
Example: "npx gaia-init" in a new project

1. User executes: npx gaia-init
   ↓
2. [gaia-init.js] starts:
   - Detects current directory
   - Scans project structure
   ↓
3. Automatic detection:
   - Finds: ./gitops → GitOps directory
   - Finds: ./terraform → Terraform directory
   - Not found: app-services
   ↓
4. Interactive questions:
   "GCP Project ID? " → user: aaxis-rnd-general
   "Primary region? " → user: us-central1
   "Cluster name? " → user: tcm-gke-non-prod
   ↓
5. Claude Code installation:
   - Verifies: claude-code not installed
   - Executes: npm install -g claude-code
   ✅ Claude Code installed
   ↓
6. Structure creation:
   - mkdir -p .claude/
   - Creates symlinks:
     .claude/agents → node_modules/.../agents
     .claude/tools → node_modules/.../tools
     .claude/hooks → node_modules/.../hooks
     .claude/commands → node_modules/.../commands
     .claude/config → node_modules/.../config
     .claude/templates → node_modules/.../templates
   ↓
7. File generation:
   - CLAUDE.md (from template)
   - AGENTS.md (symlink)
   - project-context.json (with entered data)
   ↓
8. Validation:
   ✅ All symlinks created
   ✅ CLAUDE.md generated
   ✅ project-context.json created
   ↓
9. Result:
   "
   ✅ Gaia-Ops installed successfully!
   
   Next steps:
   1. Run: claude-code
   2. Try: Ask any DevOps question
   "
```

## 📋 Available Scripts

### Installation and Setup

**`gaia-init.js`** (~1000 lines) - Main installer  
**`gaia-update.js`** (~300 lines) - Updates configuration  

### Cleanup and Uninstall

**`gaia-cleanup.js`** (~200 lines) - Cleans temporary files  
**`gaia-uninstall.js`** (~250 lines) - Complete uninstall  

### Validation

**`pre-publish-validate.js`** (~400 lines) - Pre-publish validation  
**`cleanup-claude-install.js`** (~150 lines) - Cleanup failed installations  

## 🚀 Common Usage

### First Installation

```bash
# 1. Install package
npm install @jaguilar87/gaia-ops

# 2. Run installer
npx gaia-init

# 3. Start Claude Code
claude-code
```

### Update

```bash
# Update package
npm update @jaguilar87/gaia-ops

# Postinstall hook updates automatically
```

### Uninstall

```bash
# Clean uninstall
node bin/gaia-uninstall.js
npm uninstall @jaguilar87/gaia-ops
```

## 🔧 Technical Details

### npm Binaries

Defined in `package.json`:

```json
{
  "bin": {
    "gaia-init": "bin/gaia-init.js",
    "gaia-cleanup": "bin/gaia-cleanup.js",
    "gaia-uninstall": "bin/gaia-uninstall.js"
  }
}
```

### Environment Variables

Scripts respect environment variables:

```bash
export CLAUDE_GITOPS_DIR="./my-gitops"
export CLAUDE_PROJECT_ID="my-gcp-project"

npx gaia-init --non-interactive
```

## 📖 References

**Script files:**
```
bin/
├── gaia-init.js              (~1000 lines)
├── gaia-update.js            (~300 lines)
├── gaia-cleanup.js           (~200 lines)
├── gaia-uninstall.js         (~250 lines)
├── pre-publish-validate.js   (~400 lines)
└── cleanup-claude-install.js (~150 lines)
```

**Related documentation:**
- [INSTALL.md](../INSTALL.md) - Detailed installation guide
- [README.md](../README.md) - Package overview

---

**Version:** 1.0.0  
**Last updated:** 2025-11-14  
**Total scripts:** 6 utilities  
**Maintained by:** Gaia (meta-agent) + package maintainers

