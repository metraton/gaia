# gaia-ops

Full DevOps orchestration for Claude Code. Eight specialized agents, a shared skill library, security hooks, and a planner that decomposes briefs into executable tasks. Every Bash command is classified by risk tier: read-only runs freely, state changes pause for your approval, and irreversible operations are permanently blocked.

Use this plugin when you want the complete Gaia experience — orchestrator, specialist agents (terraform, gitops, cloud-troubleshooter, developer), planner, and the full security pipeline in one install. If you only want the hooks, install `gaia-security` instead.

## Install

**Via Claude Code marketplace:**

```
/plugin marketplace add jaguilar87/gaia-ops
/plugin install gaia-ops
```

**Via npm (bundled with the full package):**

```bash
npm install @jaguilar87/gaia-ops
npx gaia-scan
```

The `gaia-scan` command detects your project stack, creates the `.claude/` structure via symlinks, and generates a starter `project-context.json`.

## Quick start

```bash
# Verify installation
npx gaia-doctor

# Detect stack and seed project-context.json
npx gaia-scan

# List queued approvals
gaia approval list

# Inspect session registry
gaia session list

# Run fast-query triage on your infrastructure
bash .claude/tools/fast-queries/run_triage.sh all
```

Inside Claude Code, you can invoke the orchestrator directly and let it dispatch to the right specialist:

```
/gaia "review the terraform module in infra/network and flag drift"
```

## What ships with this plugin

**Agents** (8): `gaia-orchestrator`, `gaia-operator`, `gaia-system`, `gaia-planner`, `developer`, `cloud-troubleshooter`, `gitops-operator`, `terraform-architect`

**Skills** (shared library): investigation, security-tiers, command-execution, agent-protocol, gaia-planner, brief-spec, terraform-patterns, gitops-patterns, developer-patterns, fast-queries, request-approval, execution, orchestrator-approval, readme-writing, skill-creation, context-updater, memory-search, memory-curation, and more.

**Hooks** (10 lifecycle events): `PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `SessionStart`, `SubagentStart`, `SubagentStop`, `Stop`, `TaskCompleted`, `PreCompact`, `PostCompact`. The pre-tool-use pipeline enforces command classification (T0-T3) and the nonce-based approval flow.

**Commands**: `/gaia` — namespaced meta-agent for system architecture, agent design, and orchestration debugging.

**CLI tools** (under `bin/`): `gaia`, `gaia-doctor`, `gaia-scan`, `gaia-status`, `gaia-history`, `gaia-review`, `gaia-metrics`, `gaia-evidence`, `gaia-cleanup`, `gaia-uninstall`.

## Permissions

This plugin requests `Bash(*)` in the allow list — the pre-tool-use hook is the actual security gate. State-changing verbs (create, delete, apply, push, commit) trigger the approval flow; irreversible commands (db drops, cluster deletes, `git push --force`, `mkfs`, `dd`) are permanently denied. Full deny list lives in `settings.json`.

Edit and Write tools are open for normal code paths. Writes to `.claude/hooks/` and `.claude/settings*.json` are hook-protected and require explicit approval regardless of session mode.

## Troubleshooting

- **Symlinks missing after install**: `npx gaia-scan` rebuilds them.
- **Multiple Claude Code installations**: `npx gaia-cleanup` removes duplicates.
- **Hook not firing**: `npx gaia-doctor` validates every manifest entry against disk.
- **Full uninstall**: `npx gaia-uninstall --force --remove-all`.

## Links

- Documentation: [github.com/metraton/gaia-ops](https://github.com/metraton/gaia-ops#readme)
- Install guide: [INSTALL.md](https://github.com/metraton/gaia-ops/blob/main/INSTALL.md)
- Issues: [github.com/metraton/gaia-ops/issues](https://github.com/metraton/gaia-ops/issues)
- License: MIT
