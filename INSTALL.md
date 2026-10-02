# Gaia Installation Guide

This guide will help you install and configure Gaia in your project. The process is non-invasive and takes less than 5 minutes.

## 🎯 What is Gaia?

Gaia is a system of specialized AI agents that automate DevOps tasks. Think of it as having a team of experts (Terraform, Kubernetes, GCP, AWS) working together, coordinated by an intelligent orchestrator.

Gaia ships as a **single, unified plugin** named `gaia` — one artifact carrying the full orchestrator, all agents, all skills, all hooks, all tools, and all config. It is distributed as the `@jaguilar87/gaia` npm package; that same package root IS the Claude Code plugin (declared in `.claude-plugin/marketplace.json` with `source: "."` -- the repository root -- and no version in the entry; the version is the one in `.claude-plugin/plugin.json`), so there is no separate `dist/` bundle.

---

## 🚀 Installation

Gaia reaches a workspace through **three channels**, the same three [README.md](./README.md) lists: the npm/pnpm package wired into Claude Code with `gaia install` (Surface 1), the Claude Code plugin from `gaia-marketplace` (Surface 2), and OpenCode on the same package (Surface 3). Pick the one that matches the host you run.

`gaia install` and `gaia dev` take the channel explicitly with `--channel`; there is no default and no `all`, and run without one they fail listing the channels. The package channel (`npm`) and the plugin exclude each other in one install folder, because each registers Gaia's hooks with Claude Code: installing one where the other is present fails, naming the channel it found and the command that removes it (`claude plugin uninstall <plugin> --scope <scope>` for the plugin, `gaia uninstall --workspace <folder>` for the package, with `--channel npm` when OpenCode is recorded beside it). OpenCode joins either, and `gaia uninstall --channel opencode` removes it on its own.

### Surface 1: npm / pnpm

Requires `python3` >= 3.12 on `PATH` (the CLI and every hook run on it). From the folder you install in, install the package, then wire that folder with `gaia install --channel npm`. Installing is one step; declaring a workspace is another, run afterwards (see [Declare a workspace, then scan it](#declare-a-workspace-then-scan-it)). A local install puts the CLI in `node_modules/.bin/`, not on your `PATH`, so it is invoked through the package manager:

```bash
npm install @jaguilar87/gaia      # or: pnpm add @jaguilar87/gaia
npx gaia install --channel npm    # or: pnpm exec gaia install --channel npm
                                  #   add --path to also write the gaia launcher to ~/.local/bin
```

With `--path`, a bare `gaia` works from any terminal afterwards; the examples below write `npx gaia` because that form works either way.

**There is no `postinstall` hook.** The install is deliberately non-invasive (npm and pnpm both handle it identically — pnpm ignores lifecycle scripts by default, so relying on `postinstall` would have been fragile). Two things bootstrap on demand instead:

- The database `~/.gaia/gaia.db` is created **lazily on the first `gaia` CLI use** (`_ensure_db_bootstrapped` in `bin/gaia`). You do not have to run anything special — the first `gaia` command you run seeds it.
- The install folder's `.claude/` structure (symlinks + `settings.local.json` + registry) is written by running `gaia install` explicitly, or by the SessionStart hook.

After install, `npx gaia doctor` verifies the result. If a bootstrap or wire-up step fails, `~/.gaia/last-install-error.json` is written with the diagnostic.

### Surface 2: Claude Code plugin

Claude Code consumes the plugin from GitHub: the marketplace is the `metraton/gaia` repository and its `gaia` entry has `source: "."`, so the plugin is the code of whatever branch or tag you add the marketplace from (`metraton/gaia#v<version>` for a release; the default branch when no ref is given). Add the marketplace (`gaia-marketplace`) and install the single plugin:

```bash
# Add the marketplace
/plugin marketplace add metraton/gaia

# Install the unified plugin (from a terminal: claude plugin install gaia@gaia-marketplace)
/plugin install gaia@gaia-marketplace
```

The marketplace route loads the agents, skills and hooks, and it does write into the install folder (it declares no workspace): the plugin's first session merges Gaia's permission set (`permissions` allow, deny and ask) and the hidden `attribution` setting into `.claude/settings.local.json` (`setup_project_permissions` in `hooks/modules/core/plugin_setup.py`), links `.claude/hooks`, then asks for `/reload-plugins` or a restart. Its sessions record those writes in `.claude/gaia-manifest.json`, the same manifest `gaia install` keeps, so `gaia uninstall` reverts them (see Uninstallation). What the plugin does **not** do is put the `gaia` CLI on your terminal's `PATH` (the orchestrator runs the plugin's own `bin/gaia`) or create the other `.claude/` links and `opencode.json`: those come from Surfaces 1 and 3. The hooks run as `sh "${CLAUDE_PLUGIN_ROOT}/hooks/launch.sh" "${CLAUDE_PLUGIN_ROOT}/hooks/<entrypoint>.py"`; the launcher uses the first of `python3`, `python` or `py -3` that is Python 3, so a Python >= 3.12 under any of those names must be on `PATH` on this route too.

Auto-update is off by default for third-party marketplaces, so a new release does not reach you on its own. Refresh the marketplace, then update the plugin:

```bash
claude plugin marketplace update gaia-marketplace
claude plugin update gaia@gaia-marketplace
```

Then restart Claude Code. The update only lands when the version in the fetched `.claude-plugin/plugin.json` differs from the installed one; the same version re-fetched installs nothing new.

For a pre-release dry-run of the plugin surface without publishing, pack the exact tarball and validate the extracted root headless:

```bash
npm run gaia:plugin-dryrun   # pack -> temp extract -> structural asserts + `claude plugin validate`
```

On the plugin surface, Claude Code reads hooks from the package root's inline `.claude-plugin/plugin.json` / `hooks/hooks.json` (generated from `build/gaia.manifest.json` at pack time) — **not** from `settings.local.json`.

### Surface 3: OpenCode

OpenCode runs on the same package as Surface 1. From the folder you install in (declaring a workspace is a separate step afterwards):

```bash
npm install @jaguilar87/gaia      # or: pnpm add @jaguilar87/gaia
npx gaia install --channel opencode
```

`--channel opencode` writes `opencode.json` pointing at the packaged `opencode/plugin.ts` instead of touching `.claude/`, so it can be added beside either Claude Code channel. A new release arrives the same way as on Surface 1: install the new package version, then `npx gaia update`, which re-wires every channel `gaia install` recorded in `.claude/gaia-manifest.json` and fails naming `gaia install --channel` when none is recorded.

OpenCode 1.18.32 runs subagents in the background only when its environment carries `OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true` (or the `OPENCODE_EXPERIMENTAL=true` umbrella); there is no `opencode.json` key for it. Gaia does not edit your shell profile, so add this line to your shell profile (`~/.bashrc`, `~/.zshrc`) and open a new shell before starting OpenCode:

```bash
export OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true
```

Without it subagents run in the foreground and resuming a subagent by `task_id` still works. `gaia install --channel opencode` prints the same line, and `gaia doctor` names it, without failing, while the variable is missing.

### Declare a workspace, then scan it

Installing wires the host; it declares no workspace and scans nothing, and neither does the plugin's first session. A workspace exists only when you declare it, and its projects are found by the scanner:

```bash
npx gaia workspace declare <name> <path>            # e.g. npx gaia workspace declare me ~/ws/me
npx gaia scan --workspace <name> <path>             # <path> defaults to the current directory
npx gaia scan --workspace <name> <path> --dry-run   # reports the classification, writes nothing
```

Outside every declared workspace, `gaia install` and the session start print `<folder> is not inside a declared workspace.` followed by `Declare one with: gaia workspace declare <name> <path>`. `gaia scan` only indexes: it never installs Gaia, creates links or declares a workspace, and applying it to an undeclared name is refused. What counts as a project, a group and the owning workspace, and what happens when a repository moves or is cloned twice, is in [README.md, Workspaces and projects](./README.md#workspaces-and-projects).

---

## 🔄 How Installation Works

### Installation Flow

```
User runs: npm install @jaguilar87/gaia   (or: pnpm add @jaguilar87/gaia)
        ↓
(no postinstall — nothing runs automatically)
        ↓
User runs: npx gaia install --channel npm    (or the SessionStart hook wires the folder)
        ↓
[Bootstrap] first `gaia` use runs scripts/bootstrap_database.py (lazy)
   - Seeds ~/.gaia/gaia.db with current schema
   - Seeds agent rows and permissions
        ↓
[Install] creates .claude/ structure
   Creates 6 directory symlinks to the gaia package:
     .claude/agents   → node_modules/.../agents
     .claude/tools    → node_modules/.../tools
     .claude/hooks    → node_modules/.../hooks
     .claude/config   → node_modules/.../config
     .claude/skills   → node_modules/.../skills
     .claude/opencode → node_modules/.../opencode
   Plus a file link:
     .claude/CHANGELOG.md → node_modules/.../CHANGELOG.md
        ↓
[Install] merges config files:
   - settings.local.json (hooks + permissions, union merge)
   - plugin-registry.json (installed[].name = "gaia")
        ↓
Validates installation:
  ✅ Symlinks correct
  ✅ DB bootstrapped
  ✅ Valid configuration
        ↓
Ready! Run: npx gaia doctor
Then declare a workspace: npx gaia workspace declare <name> <path>
and index its repositories: npx gaia scan --workspace <name> <path>
```

### Real Installation Example

```
Example: Install + scan in a project with GitOps and Terraform

1. User: pnpm add @jaguilar87/gaia   (no postinstall runs)
   ↓
2. User: pnpm exec gaia install --channel npm
   ✅ ~/.gaia/gaia.db bootstrapped (lazy, on first `gaia` use)
   ✅ .claude/ created
   ✅ 6 directory symlinks + CHANGELOG.md link created
      (agents, tools, hooks, config, skills, opencode)
   ✅ settings.local.json merged
   ✅ plugin-registry.json written (name: gaia)
   ✅ workspace: the declared workspace holding the folder,
      or "Declare one with: gaia workspace declare <name> <path>"
   ↓
3. Result -- next steps:
   1. Run: pnpm exec gaia doctor
   2. Run: pnpm exec gaia workspace declare work ~/work
      and: pnpm exec gaia scan --workspace work ~/work
   3. Run: claude
   4. Ask: "Show me GKE clusters"
```

---

## ⚙️ Installation Options

The options each command accepts are the ones its `--help` prints; the ones this guide uses:

```
gaia install --channel {npm,opencode} [--path] [--workspace W]
                                 # bootstrap DB + wire the install folder (no postinstall)
gaia update                      # re-wires the recorded channels after a package upgrade
gaia workspace declare NAME PATH # declare a workspace rooted at PATH
gaia scan --workspace NAME [--dry-run] [root]
                                 # index the git repositories under root
gaia project move PROJECT --into WORKSPACE [--from WORKSPACE] [--dry-run]
                                 # move a project into another declared workspace
gaia uninstall [--channel npm|plugin|opencode] [--workspace W] [--dry-run] [--no-backup]
                                 # revert what install wrote (see Uninstallation)
gaia doctor [--workspace PATH] [--fix]
```

---

## 📦 What Gets Installed?

### Created Structure

```
your-project/
├── .claude/                       ← Created by gaia install
│   ├── agents/ (symlink)          → Agent definitions
│   ├── skills/ (symlink)          → Skill modules
│   ├── tools/ (symlink)           → Orchestration tools
│   ├── hooks/ (symlink)           → Security validations
│   ├── config/ (symlink)          → Configuration (contracts, rules)
│   ├── opencode/ (symlink)        → OpenCode plugin
│   ├── CHANGELOG.md (file link)    → Package changelog
│   ├── logs/                      ← Audit logs
│   ├── approvals/                 ← Pending T3 approval files
│   ├── plugin-registry.json       ← installed[].name = "gaia"
│   └── settings.local.json        ← Merged hooks + permissions + env
└── node_modules/
    └── @jaguilar87/gaia/          ← npm package (single unified plugin)

~/.gaia/
└── gaia.db                        ← Canonical context + memory store (SQLite)
```

Six directory symlinks (`agents`, `tools`, `hooks`, `config`, `skills`, `opencode`) plus one `CHANGELOG.md` file link — the canonical list is `_SYMLINK_NAMES` + `_SYMLINK_FILES` in `bin/cli/_install_helpers.py`.

Project context (stack, GitOps layout, Terraform layout, etc.) lives in `~/.gaia/gaia.db`, not in `.claude/project-context/`. `npx gaia scan --workspace <name>` re-indexes it and `npx gaia context show` inspects it.

**Wire-up verification:** after install, the same checklist applies to every install mode (live, npm-sandbox, plugin, registry). See `skills/gaia-verify/SKILL.md` → "Wire-up checklist".

---

## 📚 Documentation Available After Installation

Once installed, you have access to **complete documentation** in each directory:

### Directory READMEs

```
.claude/
├── agents/               9 agents (platform-architect, gitops-operator, etc.)
├── skills/README.md      37 skill modules
├── config/README.md      Contracts, git standards, surface routing
├── hooks/README.md       Hook scripts (primary + event handlers)
├── tools/                Context, memory, validation, review
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
npx gaia context show

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

# Or, from the terminal, refresh the project context:
npx gaia scan --workspace <name>
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
npm install @jaguilar87/gaia@latest   # or: pnpm add @jaguilar87/gaia@latest

# 2. Re-sync the install folder (no postinstall does this for you):
npx gaia update                       # or: pnpm exec gaia update
#    - Refreshes DB schema, config, and symlinks after the version bump
#    - Re-wires every channel recorded in .claude/gaia-manifest.json;
#      fails naming gaia install --channel when none is recorded
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

Remove the extra copy by hand. (`gaia cleanup` is not for this: it removes Gaia's own links and markers from the workspace and runs data retention.)

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

**Solution:** remove the extra copy by hand, as in "If You Have Multiple Installations" above.

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

# Re-run install (idempotent, re-entrant)
npx gaia install --channel npm

# Or, if the DB itself is missing, just run any gaia command
# (lazy bootstrap re-creates it):
npx gaia doctor
```

For the full symptom → cause → fix table, see `skills/gaia-release/reference.md` → "Diagnostic guide".

---

## 🧹 Uninstallation

Each channel takes back only what it wrote, and `~/.gaia/gaia.db` is never touched: delete `~/.gaia/` yourself if you want the memory gone too.

### Package (Surface 1) and OpenCode (Surface 3)

Run `gaia uninstall` **before** removing the package, while the CLI still exists. `npm uninstall` does not do it for you: npm >= 7 does not run a package's `preuninstall` script, and pnpm does not run lifecycle scripts by default.

```bash
npx gaia uninstall --dry-run          # shows what reverts, changes nothing
npx gaia uninstall                    # OpenCode-only folder: add --workspace <folder>
npx gaia uninstall --channel opencode # beside the plugin: takes back OpenCode only
npm uninstall @jaguilar87/gaia        # or: pnpm remove @jaguilar87/gaia
```

Without `--channel`, `gaia uninstall` takes back every channel the manifest records. `--channel npm|plugin|opencode` takes back one and leaves the others recorded: OpenCode owns `opencode.json` and `.opencode/`, npm or the plugin the rest of the manifest, and the package copy under `node_modules` stays while a remaining channel runs from it. A channel the manifest does not record fails, naming the recorded ones, and changes nothing.

`gaia uninstall` reverts `.claude/gaia-manifest.json`: every file, link and settings key `gaia install` wrote returns to its prior state (`opencode.json` and the `--path` launcher included), files Gaia did not create are never removed, and a gzip snapshot of the database goes to `~/.gaia/snapshots/` unless `--no-backup`. An OpenCode-only folder has no `.claude/` to detect, hence `--workspace`. Do not delete `.claude/` by hand: it also holds your own settings and anything else you or other tools put there.

It also removes what the package manager and `gaia dev` left, which the manifest cannot record: the Gaia package under `node_modules` and its `.bin` shim, Gaia's line in `package.json` and `package-lock.json` (your other dependencies stay), the tarballs `gaia dev` cached for the workspace, and hook scratch state. After `gaia dev --channel plugin`, this run and `gaia uninstall --channel plugin` also take back that channel's setup: `gaia@gaia-dev` and the `gaia-dev` marketplace are removed from local scope through the `claude` CLI, `gaia@gaia-dev` leaves `.claude/settings.local.json`, and the build extracted under Gaia's cache (`dev-plugin/<workspace>`) is deleted. Without the `claude` CLI, the two commands to run are listed as left in place and the build stays, so the marketplace never points at a deleted folder. `gaia@gaia-marketplace` gets back the entry it had before `gaia dev` disabled it only when `gaia dev` recorded that entry (a `gaia dev` older than this release did not) and the entry is still the `false` it wrote; otherwise it is left as it is and listed as left in place with the command that enables it, `claude plugin enable gaia@gaia-marketplace --scope local`. A `gaia@gaia-dev` entry with neither the build nor that record is yours and stays. What it cannot fix itself is listed as left in place, with the reason: a `pnpm-lock.yaml` or `yarn.lock` is regenerated by its own package manager, and `.claude/logs` is history you may want. Once it has run, the `npm uninstall` line above finds nothing left to remove. `--dry-run` lists exactly what the real run does.

### Claude Code plugin (Surface 2)

Run `gaia uninstall` in the install folder **before** `claude plugin uninstall`, while the plugin's `gaia` still exists. The plugin does not put `gaia` on your terminal's `PATH`, so run its own copy: `claude plugin list --json` prints an `installPath` for each `gaia@gaia-marketplace` install; take the one installed for this folder (a local-scope install names it in `projectPath`) and run `<installPath>/bin/gaia uninstall` from a terminal in the install folder -- it needs only Python on `PATH`. Inside a Claude Code session in the install folder the same `bin/gaia` is on the Bash tool's `PATH`, so Gaia can run `gaia uninstall` there for you; with the package installed too, `npx gaia uninstall` does the same.

```bash
<installPath>/bin/gaia uninstall --dry-run          # shows what reverts, changes nothing
<installPath>/bin/gaia uninstall                    # reverts the plugin's writes into the install folder
claude plugin uninstall gaia@gaia-marketplace
claude plugin marketplace remove gaia-marketplace   # optional
```

The plugin's sessions record what they write into the install folder in `.claude/gaia-manifest.json` -- the permissions and `attribution` merged into `.claude/settings.local.json`, the `.claude/hooks` link -- so `gaia uninstall` reverts them, restores the user entries the merge replaced, and keeps what you added since. A plugin install folder from before that record is recognized by Gaia's permissions and attribution.

With OpenCode installed in the same workspace, `gaia uninstall` without a channel removes OpenCode too. Use `gaia uninstall --channel plugin` to take back only the Claude Code side and keep OpenCode, or `--channel opencode` to remove OpenCode and keep the plugin. npm and the plugin share `.claude/`, so uninstalling either takes back every Claude Code entry in the manifest; a plugin still enabled re-records its own writes on its next session.

---

## 💡 Design Principles

Gaia is designed with these principles:

✅ **Minimal** - Only creates what's needed, no duplicates  
✅ **Adaptive** - Auto-detects existing installations  
✅ **Non-invasive** - No postinstall; bootstrap is lazy, works under npm and pnpm alike  
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
A: Yes. Every git repository is a project, and one install serves them all. Gather them under workspaces you declare (`npx gaia workspace declare <name> <path>`) and index each with `npx gaia scan --workspace <name> <path>`; a repository belongs to the nearest declared workspace that contains it. One database, `~/.gaia/gaia.db`, holds every workspace. See [README.md, Workspaces and projects](./README.md#workspaces-and-projects).

**Q: Do symlinks work on Windows?**  
A: Yes, but you need to enable developer mode or run as administrator.

**Q: How do I update only documentation without changing code?**
A: `npm install @jaguilar87/gaia@latest` then `npx gaia update` - symlinks point to the new version automatically.

---

**Version:** the current release (`version` in `package.json`)
**Last updated:** 2026-09-24
**Maintained by:** Jorge Aguilar + Gaia (meta-agent)
