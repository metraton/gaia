# Gaia-Ops Installation Guide

This guide will help you install and configure Gaia-Ops in your project. The process is automatic and takes less than 5 minutes.

## 🎯 What is Gaia-Ops?

Gaia-Ops is a system of specialized AI agents that automate DevOps tasks. Think of it as having a team of experts (Terraform, Kubernetes, GCP, AWS) working together, coordinated by an intelligent orchestrator.

---

## 🚀 Quick Installation (Recommended)

### Option 1: Interactive Installation

The easiest way - the installer will guide you step by step:

```bash
npx gaia-scan
```

It will ask questions like:
- Where are your GitOps files?
- Where is your Terraform code?
- What is your GCP project?

### Option 2: Non-Interactive Installation

For CI/CD scripts or if you already know the values:

```bash
npx gaia-scan --non-interactive \
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
User runs: npx gaia-scan
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
  .claude/skills → node_modules/.../skills
  .claude/speckit → node_modules/.../speckit
  .claude/templates → node_modules/.../templates
         ↓
Generates config files:
  - project-context.json
  - settings.json
         ↓
Validates installation:
  ✅ Symlinks correct
  ✅ Claude Code available
  ✅ Valid configuration
         ↓
Ready! You can use: claude
```

### Real Installation Example

```
Example: Installation in project with GitOps and Terraform

1. User: npx gaia-scan
   ↓
2. Detector finds:
   ✅ ./gitops (52 YAML files detected)
   ✅ ./terraform (15 .tf files detected)
   ❌ ./app-services (not found)
   ↓
3. Installer asks:
   ? GCP Project ID: → my-gcp-project
   ? Primary region: → us-central1
   ? Cluster name: → my-gke-cluster
   ↓
4. Checks Claude Code:
   ✅ Claude Code already installed at /usr/local/bin/claude
   ⏭️  Skipping reinstall
   ↓
5. Creates structure:
   ✅ .claude/ created
   ✅ 8 symlinks created
   ✅ project-context.json created
   ✅ settings.json created
   ↓
6. Result:
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   ✅ Gaia-Ops installed successfully!
   
   📚 Documentation available:
   - .claude/agents/README.md
   - .claude/config/README.md
   - .claude/commands/README.md
   
   🚀 Next steps:
   1. Run: claude
   2. Ask: "Show me GKE clusters"
   3. Or use: /scan-project to detect your project stack
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
npx gaia-scan --non-interactive
```

### Complete CLI Options

```
gaia-scan [options]

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
│   ├── agents/ (symlink)       → Agent definitions
│   ├── skills/ (symlink)       → Skill modules
│   ├── tools/ (symlink)        → Orchestration tools
│   ├── hooks/ (symlink)        → Security validations
│   ├── commands/ (symlink)     → Slash commands
│   ├── config/ (symlink)       → Configuration (contracts, rules)
│   ├── templates/ (symlink)    → Installation templates
│   ├── speckit/ (symlink)      → Spec-Kit framework
│   ├── project-context/        ← Your project context (SSOT)
│   ├── logs/                   ← Audit logs
│   └── settings.json           ← Security configuration
└── node_modules/
    └── @jaguilar87/gaia-ops/   ← npm package
```

---

## 📚 Documentation Available After Installation

Once installed, you have access to **complete documentation** in each directory:

### Directory READMEs

```
.claude/
├── agents/               6 agents (terraform-architect, gitops-operator, etc.)
├── skills/README.md      20 skill modules
├── commands/README.md    6 slash commands (5 speckit + scan-project)
├── config/README.md      Contracts, git standards, universal rules
├── hooks/README.md       8 hook scripts (4 primary + 4 event handlers)
├── tools/                Context, memory, validation, review
├── speckit/README.md     Spec-Kit framework
├── templates/README.md   Installation templates
└── bin/README.md         CLI utilities
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
# View project-context.json
cat .claude/project-context/project-context.json

# View settings.json
cat .claude/settings.json
```

### 3. Start Claude Code

```bash
claude
```

### 4. Test the System

```bash
# In Claude Code, try:
"Show me GKE clusters"
"List deployments in production namespace"

# Or use slash commands:
/scan-project
```

---

## 🔄 Package Updates

### ⚠️ Files That Get Overwritten

When you update `@jaguilar87/gaia-ops`, these files are **regenerated from templates**:

| File | Behavior | Recommended Action |
|------|----------|-------------------|
| `.claude/settings.json` | ⚠️ **Replaced** from template (source of truth) | Safe |
| `.claude/project-context/project-context.json` | ✅ **Preserved** | Safe |
| `.claude/logs/` | ✅ **Preserved** | Safe |
| Other `.claude/` files | ✅ **Auto-updated via symlinks** | Safe |

Orchestrator identity lives in `agents/gaia-orchestrator.md` and is activated via `settings.json: { "agent": "gaia-orchestrator" }` -- no `CLAUDE.md` is generated.

### Update Process

```bash
# 1. Update package
npm install @jaguilar87/gaia-ops@latest

# 2. Postinstall hook automatically:
#    - Replaces settings.json from template
#    - Fixes broken symlinks
```

---

## 🛠️ Claude Code Management

### Avoiding Multiple Installations

Gaia-Ops **automatically detects** if you already have Claude Code installed and **does NOT reinstall it**.

#### Installation Verification

```bash
# See where Claude Code is installed
which claude

# Should show ONE location:
# ✅ /usr/local/bin/claude (native - recommended)
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
which claude
claude --version
```

---

## 🐛 Troubleshooting

### Problem: Claude Code Not Found

**Solution:**
```bash
# Verify installation
which claude

# If not found, install via npm
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
npx gaia-scan
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

# 2. Uninstall npm package
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

**Q: How do I update only documentation without changing code?**
A: `npm update @jaguilar87/gaia-ops` - symlinks point to the new version automatically.

---

**Version:** 4.7.2
**Last updated:** 2026-04-09
**Maintained by:** Jorge Aguilar + Gaia (meta-agent)

