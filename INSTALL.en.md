# Gaia-Ops Installation Guide

**[🇪🇸 Versión en español](INSTALL.md)**

This guide will help you install and configure Gaia-Ops in your project. The process is automatic and takes less than 5 minutes.

## 🎯 What is Gaia-Ops?

Gaia-Ops is a system of specialized AI agents that automate DevOps tasks. Think of it as having a team of experts (Terraform, Kubernetes, GCP, AWS) working together, coordinated by an intelligent orchestrator.

---

## 🚀 Quick Installation (Recommended)

### Option 1: Interactive Installation

The easiest way - the installer will guide you step by step:

```bash
npx gaia-init
```

It will ask questions like:
- Where are your GitOps files?
- Where is your Terraform code?
- What is your GCP project?

### Option 2: Non-Interactive Installation

For CI/CD scripts or if you already know the values:

```bash
npx gaia-init --non-interactive \
  --gitops ./gitops \
  --terraform ./terraform \
  --app-services ./app-services \
  --project-id my-gcp-project \
  --cluster my-gke-cluster
```

---

## 🔄 How Installation Works

### Installation Flow

```
User runs: npx gaia-init
        ↓
[Detector] scans your project
        ↓
   Finds automatically:
   - GitOps directory
   - Terraform directory
   - Apps directory
        ↓
[Installer] asks for missing data:
   - GCP Project ID
   - Region
   - Cluster name
        ↓
[Installer] checks Claude Code
        ↓
    Already installed?
    ┌────┴────┐
    ↓         ↓
  YES        NO
    ↓         ↓
  Use     Install
 existing    new
    ↓         ↓
    └────┬────┘
         ↓
Creates .claude/ structure
         ↓
Creates symlinks to gaia-ops:
  .claude/agents → node_modules/.../agents
  .claude/tools → node_modules/.../tools
  .claude/hooks → node_modules/.../hooks
  .claude/commands → node_modules/.../commands
  .claude/config → node_modules/.../config
         ↓
Generates config files:
  - CLAUDE.md (orchestrator)
  - AGENTS.md (symlink)
  - project-context.json
  - settings.json
         ↓
Validates installation:
  ✅ Symlinks correct
  ✅ Claude Code available
  ✅ Valid configuration
         ↓
Ready! You can use: claude-code
```

### Real Installation Example

```
Example: Installation in project with GitOps and Terraform

1. User: npx gaia-init
   ↓
2. Detector finds:
   ✅ ./gitops (52 YAML files detected)
   ✅ ./terraform (15 .tf files detected)
   ❌ ./app-services (not found)
   ↓
3. Installer asks:
   ? GCP Project ID: → aaxis-rnd-general-project
   ? Primary region: → us-central1
   ? Cluster name: → tcm-gke-non-prod
   ↓
4. Checks Claude Code:
   ✅ Claude Code already installed at /usr/local/bin/claude
   ⏭️  Skipping reinstall
   ↓
5. Creates structure:
   ✅ .claude/ created
   ✅ 6 symlinks created
   ✅ CLAUDE.md generated (196 lines)
   ✅ project-context.json created
   ↓
6. Result:
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   ✅ Gaia-Ops installed successfully!
   
   📚 Documentation available:
   - .claude/agents/README.md
   - .claude/config/README.md
   - .claude/commands/README.md
   
   🚀 Next steps:
   1. Run: claude-code
   2. Ask: "Show me GKE clusters"
   3. Or use: /gaia to explore the system
```

---

## ⚙️ Installation Options

### Environment Variables

Configure before installing to avoid questions:

```bash
# Configure paths
export CLAUDE_GITOPS_DIR="./gitops"
export CLAUDE_TERRAFORM_DIR="./terraform"
export CLAUDE_APP_SERVICES_DIR="./app-services"

# Configure project
export CLAUDE_PROJECT_ID="my-gcp-project"
export CLAUDE_REGION="us-central1"
export CLAUDE_CLUSTER_NAME="my-gke-cluster"

# Install without questions
npx gaia-init --non-interactive
```

### Complete CLI Options

```
gaia-init [options]

Options:
  --non-interactive          Skip prompts, use provided values or defaults
  --gitops <path>           GitOps directory path
  --terraform <path>        Terraform directory path
  --app-services <path>     Applications directory path
  --project-id <id>         GCP project ID
  --region <region>         Primary region (default: us-central1)
  --cluster <name>          Cluster name
  --skip-claude-install     Skip Claude Code installation
```

---

## 📦 What Gets Installed?

### Created Structure

```
your-project/
├── .claude/                    ← New directory
│   ├── agents/ (symlink)       → Specialized agents
│   ├── tools/ (symlink)        → Orchestration tools
│   ├── hooks/ (symlink)        → Security validations
│   ├── commands/ (symlink)     → Slash commands
│   ├── config/ (symlink)       → Configuration and docs
│   ├── templates/ (symlink)    → Installation templates
│   ├── project-context.json    ← Your configuration
│   ├── logs/                   ← Audit logs
│   └── tests/                  ← System tests
├── CLAUDE.md                   ← Main orchestrator
├── AGENTS.md (symlink)         ← System overview
└── node_modules/
    └── @jaguilar87/gaia-ops/   ← npm package
```

---

## 📚 Documentation Available After Installation

Once installed, you have access to **complete documentation** in each directory:

### Directory READMEs

```
.claude/
├── agents/README.md              6 specialist agents system
├── commands/README.md            11 available slash commands
├── config/README.md              17 configuration files
├── hooks/README.md               7 security hooks
├── tools/README.md               Orchestration tools
└── templates/README.md           Installation templates
```

**All with English version:** `.../README.en.md`

### Special Documentation

- **Documentation Principles:** `.claude/config/documentation-principles.md`
- **Orchestration Workflow:** `.claude/config/orchestration-workflow.md`
- **Agent Catalog:** `.claude/config/agent-catalog.md`

### How to Navigate Documentation?

```bash
# View agents documentation
cat .claude/agents/README.md

# View available commands
cat .claude/commands/README.md

# View system configuration
cat .claude/config/README.md
```

---

## ✅ Post-Installation

### 1. Verify Installation

```bash
# Check created structure
ls -la .claude/

# Should show symlinks:
# agents -> ../node_modules/@jaguilar87/gaia-ops/agents
# tools -> ../node_modules/@jaguilar87/gaia-ops/tools
```

### 2. Review Generated Configuration

```bash
# View generated CLAUDE.md
cat CLAUDE.md

# View project-context.json
cat .claude/project-context.json
```

### 3. Start Claude Code

```bash
claude-code
```

### 4. Test the System

```bash
# In Claude Code, try:
"Show me GKE clusters"
"List deployments in production namespace"

# Or use slash commands:
/gaia Explain how routing works
/save-session my-session
/session-status
```

---

## 🔄 Package Updates

### ⚠️ Files That Get Overwritten

When you update `@jaguilar87/gaia-ops`, these files are **regenerated from templates**:

| File | Behavior | Recommended Action |
|------|----------|-------------------|
| `CLAUDE.md` | ⚠️ **Overwritten** | Backup if you customize |
| `.claude/settings.json` | ⚠️ **Overwritten** | Backup if you customize |
| `.claude/project-context.json` | ✅ **Preserved** | Safe |
| `.claude/logs/` | ✅ **Preserved** | Safe |
| Other `.claude/` files | ✅ **Auto-updated via symlinks** | Safe |

### Update Process

```bash
# 1. Backup (optional, if you customized)
cp CLAUDE.md CLAUDE.md.backup
cp .claude/settings.json .claude/settings.json.backup

# 2. Update package
npm install @jaguilar87/gaia-ops@latest

# 3. Postinstall hook automatically regenerates:
#    - CLAUDE.md
#    - .claude/settings.json

# 4. If you made backup, compare and merge changes
diff CLAUDE.md CLAUDE.md.backup
```

---

## 🛠️ Claude Code Management

### Avoiding Multiple Installations

Gaia-Ops **automatically detects** if you already have Claude Code installed and **does NOT reinstall it**.

#### Installation Verification

```bash
# See where Claude Code is installed
which claude-code

# Should show ONE location:
# ✅ /usr/local/bin/claude-code (native - recommended)
```

#### If You Have Multiple Installations

**Option 1: Automatic Cleanup**
```bash
npx gaia-cleanup
```

**Option 2: Manual Cleanup**
```bash
# Remove npm global installation (if exists)
npm -g uninstall @anthropic-ai/claude-code

# Verify only one remains
which claude-code
claude-code --version
```

---

## 🐛 Troubleshooting

### Problem: Claude Code Not Found

**Solution:**
```bash
# Verify installation
which claude-code

# If not found, install manually
npm install -g @anthropic-ai/claude-code
```

---

### Problem: Multiple Claude Code Installations

**Solution:**
```bash
# Automatic cleanup
npx gaia-cleanup
```

---

### Problem: Permission Denied on npm global

**Solution (recommended):**
```bash
mkdir ~/.npm-global
npm config set prefix '~/.npm-global'
export PATH=~/.npm-global/bin:$PATH
echo 'export PATH=~/.npm-global/bin:$PATH' >> ~/.bashrc
```

---

### Problem: Symlinks Not Created

**Solution:**
```bash
# Re-run installation
npx gaia-init --force
```

---

## 🧹 Uninstallation

### Complete Uninstallation

```bash
# Interactive script (recommended)
npx gaia-uninstall

# Forced uninstall (no questions)
npx gaia-uninstall --force --remove-all
```

### Manual Uninstallation

```bash
# 1. Remove .claude/ directory
rm -rf .claude/

# 2. Remove generated files
rm CLAUDE.md AGENTS.md

# 3. Uninstall npm package
npm uninstall @jaguilar87/gaia-ops
```

---

## 💡 Design Principles

Gaia-Ops is designed with these principles:

✅ **Minimal** - Only creates what's needed, no duplicates  
✅ **Adaptive** - Auto-detects existing installations  
✅ **Non-invasive** - Works from any directory  
✅ **Safe** - Validates paths and skips reinstalls  
✅ **Clear** - Explicit feedback on each step  
✅ **Documented** - Complete documentation in each directory  

---

## 📞 Support

### Resources

- **Documentation:** Inside `.claude/*/README.md`
- **Issues:** https://github.com/metraton/gaia-ops/issues
- **Email:** jaguilar1897@gmail.com

### Frequently Asked Questions

**Q: Can I use gaia-ops in multiple projects?**  
A: Yes. Install in each project and each will have its own `project-context.json`.

**Q: Do symlinks work on Windows?**  
A: Yes, but you need to enable developer mode or run as administrator.

**Q: Can I customize CLAUDE.md without it being overwritten?**  
A: Not directly. Better: contribute changes to the template in the repository.

**Q: How do I update only documentation without changing code?**  
A: `npm update @jaguilar87/gaia-ops` - symlinks point to the new version automatically.

---

**Version:** 2.6.0  
**Last updated:** 2025-11-14  
**Maintained by:** Jorge Aguilar + Gaia (meta-agent)

