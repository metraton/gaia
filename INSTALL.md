# Gaia Installation Guide

This guide will help you install and configure Gaia in your project. The process is automatic and takes less than 5 minutes.

## 🎯 What is Gaia?

Gaia is a system of specialized AI agents that automate DevOps tasks. Think of it as having a team of experts (Terraform, Kubernetes, GCP, AWS) working together, coordinated by an intelligent orchestrator.

The `gaia-ops` sub-plugin ships the full orchestrator and all agents; `gaia-security` ships security hooks only. Both are distributed via the `@jaguilar87/gaia` npm package.

---

## 🚀 Quick Installation (Recommended)

### Option 1: npm install (standard)

The npm `postinstall` hook does everything automatically -- bootstraps the DB, creates `.claude/`, writes symlinks, registers the plugin, and merges hook config:

```bash
npm install @jaguilar87/gaia
```

After install, `gaia doctor` verifies the result. If postinstall fails, `~/.gaia/last-install-error.json` is written with the diagnostic.

### Option 2: Project Scanner (on-demand)

To detect or refresh your project context (stack, GitOps directory, Terraform layout, GCP project, etc.) -- this is **not** the installer, it writes scan results to `~/.gaia/gaia.db`:

```bash
gaia scan
```

Or non-interactive:

```bash
gaia scan --non-interactive \
  --gitops ./gitops \
  --terraform ./terraform \
  --app-services ./app-services \
  --project-id my-gcp-project \
  --cluster my-gke-cluster
```

**Important:** `gaia scan` and `gaia install` are separate flows. `gaia install` (run automatically by `npm install` postinstall) bootstraps the database and `.claude/` structure. `gaia scan` detects your project stack and writes the results to the DB. Running `gaia scan` never installs or creates symlinks; running `gaia install` never scans. To bootstrap from scratch, use `gaia install` (or `npm install @jaguilar87/gaia`), not `gaia scan`.

---

## 🔄 How Installation Works

### Installation Flow

```
User runs: npm install @jaguilar87/gaia
        ↓
postinstall script → gaia install --postinstall
        ↓
[Bootstrap] runs scripts/bootstrap_database.sh
   - Seeds ~/.gaia/gaia.db with current schema (v16)
   - Seeds agent rows and permissions
        ↓
[Install] creates .claude/ structure
   Creates symlinks to gaia package:
     .claude/agents    → node_modules/.../agents
     .claude/tools     → node_modules/.../tools
     .claude/hooks     → node_modules/.../hooks
     .claude/commands  → node_modules/.../commands
     .claude/config    → node_modules/.../config
     .claude/skills    → node_modules/.../skills
     .claude/templates → node_modules/.../templates
        ↓
[Install] merges config files:
   - settings.local.json (hooks + permissions, union merge)
   - plugin-registry.json (installed[].name = "gaia-ops")
        ↓
Validates installation:
  ✅ Symlinks correct
  ✅ DB bootstrapped
  ✅ Valid configuration
        ↓
Ready! Run: gaia doctor
Then optionally scan your project stack: gaia scan
```

### Real Installation Example

```
Example: Install + scan in a project with GitOps and Terraform

1. User: npm install @jaguilar87/gaia
   ↓
2. postinstall runs gaia install --postinstall:
   ✅ ~/.gaia/gaia.db bootstrapped (schema v16)
   ✅ .claude/ created
   ✅ 7 symlinks created (agents, tools, hooks, commands, templates, config, skills)
   ✅ settings.local.json merged
   ✅ plugin-registry.json written (name: gaia-ops)
   ↓
3. User: gaia scan (optional -- detects project stack)
   ↓
4. Detector finds:
   ✅ ./gitops (52 YAML files detected)
   ✅ ./terraform (15 .tf files detected)
   ❌ ./app-services (not found)
   ↓
5. Scanner writes to ~/.gaia/gaia.db:
   ✅ project_identity, stack, git, infrastructure sections recorded
   ✅ No project-context.json file generated (DB is canonical)
   ↓
6. Result:
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   ✅ Gaia installed and project scanned!
   
   Next steps:
   1. Run: gaia doctor
   2. Run: claude
   3. Ask: "Show me GKE clusters"
   4. Or use: /scan-project to re-scan your project stack
```

---

## ⚙️ Installation Options

### Environment Variables

Configure before scanning to avoid prompts:

```bash
# Configure paths
export CLAUDE_GITOPS_DIR="./gitops"
export CLAUDE_TERRAFORM_DIR="./terraform"
export CLAUDE_APP_SERVICES_DIR="./app-services"

# Configure project
export CLAUDE_PROJECT_ID="my-gcp-project"
export CLAUDE_REGION="us-central1"
export CLAUDE_CLUSTER_NAME="my-gke-cluster"

# Scan without questions
gaia scan --non-interactive
```

### Complete CLI Options

```
gaia install [options]          # Bootstrap DB + .claude/ structure (also: npm postinstall)

gaia scan [options]             # Detect project stack, write to ~/.gaia/gaia.db

gaia scan options:
  --non-interactive          Skip prompts, use provided values or defaults
  --gitops <path>           GitOps directory path
  --terraform <path>        Terraform directory path
  --app-services <path>     Applications directory path
  --project-id <id>         GCP project ID
  --region <region>         Primary region (default: us-central1)
  --cluster <name>          Cluster name
```

---

## 📦 What Gets Installed?

### Created Structure

```
your-project/
├── .claude/                       ← Created by gaia install (npm postinstall)
│   ├── agents/ (symlink)          → Agent definitions
│   ├── skills/ (symlink)          → Skill modules
│   ├── tools/ (symlink)           → Orchestration tools
│   ├── hooks/ (symlink)           → Security validations
│   ├── commands/ (symlink)        → Slash commands
│   ├── config/ (symlink)          → Configuration (contracts, rules)
│   ├── templates/ (symlink)       → Installation templates
│   ├── logs/                      ← Audit logs
│   ├── approvals/                 ← Pending T3 approval files
│   ├── plugin-registry.json       ← installed[].name = "gaia-ops"
│   └── settings.local.json        ← Merged hooks + permissions + env
└── node_modules/
    └── @jaguilar87/gaia/          ← npm package

~/.gaia/
└── gaia.db                        ← Canonical context + memory store (SQLite, schema v16)
```

Project context (stack, GitOps layout, Terraform layout, etc.) lives in `~/.gaia/gaia.db`, not in `.claude/project-context/`. Run `gaia scan` to populate it and `gaia context show` to inspect it.

**Wire-up verification:** after install, the same checklist applies to every install mode (live, dry-run, RC, stable). See `skills/gaia-release/SKILL.md` -> "Wire-up Verification Checklist".

---

## 📚 Documentation Available After Installation

Once installed, you have access to **complete documentation** in each directory:

### Directory READMEs

```
.claude/
├── agents/               6 agents (platform-architect, gitops-operator, etc.)
├── skills/README.md      20 skill modules
├── commands/README.md    Slash commands (gaia-plan, scan-project)
├── config/README.md      Contracts, git standards, surface routing
├── hooks/README.md       8 hook scripts (4 primary + 4 event handlers)
├── tools/                Context, memory, validation, review
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
# agents -> ../node_modules/@jaguilar87/gaia/agents
# tools -> ../node_modules/@jaguilar87/gaia/tools
```

### 2. Review Generated Configuration

```bash
# View project context (stored in DB)
gaia context show

# View settings
cat .claude/settings.local.json
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

When you update `@jaguilar87/gaia`, these files are **regenerated from templates**:

| File / Store | Behavior | Recommended Action |
|------|----------|-------------------|
| `.claude/settings.local.json` | ✅ **Union merged** -- never removes user config | Safe |
| `~/.gaia/gaia.db` | ✅ **Migrated in place** -- schema bumped, data preserved | Safe |
| `.claude/logs/` | ✅ **Preserved** | Safe |
| Other `.claude/` files | ✅ **Auto-updated via symlinks** | Safe |

Orchestrator identity lives in `agents/gaia-orchestrator.md` and is activated via `settings.json: { "agent": "gaia-orchestrator" }` -- no `CLAUDE.md` is generated.

### Update Process

```bash
# 1. Update package
npm install @jaguilar87/gaia@latest

# 2. Postinstall hook automatically:
#    - Replaces settings.json from template
#    - Fixes broken symlinks
```

---

## 🛠️ Claude Code Management

### Avoiding Multiple Installations

Gaia **automatically detects** if you already have Claude Code installed and **does NOT reinstall it**.

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
gaia cleanup
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
gaia cleanup
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
# Check the diagnostic marker first
cat ~/.gaia/last-install-error.json

# Re-run install (postinstall is re-entrant)
npm install @jaguilar87/gaia

# Or repair without a fresh tarball
gaia install
```

For the full symptom -> cause -> fix table, see `skills/gaia-release/reference.md` -> "Diagnostic Guide".

---

## 🧹 Uninstallation

### Complete Uninstallation

```bash
# Interactive script (recommended)
gaia uninstall

# Forced uninstall (no questions)
gaia uninstall --force --remove-all
```

### Manual Uninstallation

```bash
# 1. Remove .claude/ directory
rm -rf .claude/

# 2. Uninstall npm package
npm uninstall @jaguilar87/gaia
```

---

## 💡 Design Principles

Gaia is designed with these principles:

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
- **Issues:** https://github.com/metraton/gaia/issues
- **Email:** jorge.aguilar87@gmail.com

### Frequently Asked Questions

**Q: Can I use Gaia in multiple projects?**  
A: Yes. Each project is a separate workspace in `~/.gaia/gaia.db`. Run `gaia scan` inside each project directory to populate its context. The DB is shared but context is per-workspace.

**Q: Do symlinks work on Windows?**  
A: Yes, but you need to enable developer mode or run as administrator.

**Q: How do I update only documentation without changing code?**
A: `npm update @jaguilar87/gaia` - symlinks point to the new version automatically.

---

**Version:** 5.0.0-rc.7
**Last updated:** 2026-05-22
**Maintained by:** Jorge Aguilar + Gaia (meta-agent)

