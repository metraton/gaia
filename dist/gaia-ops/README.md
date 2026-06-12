# gaia-ops

Full DevOps orchestration for Claude Code. Eight specialized agents, a shared skill library, security hooks, and a planner that decomposes briefs into executable tasks. Every Bash command is classified by risk tier: read-only runs freely, state changes pause for your approval, and irreversible operations are permanently blocked.

Use this plugin when you want the complete Gaia experience — orchestrator, specialist agents (terraform, gitops, cloud-troubleshooter, developer), planner, and the full security pipeline in one install. If you only want the hooks, install `gaia-security` instead.

## Install

**Via Claude Code marketplace:**

```
/plugin marketplace add metraton/gaia
/plugin install gaia-ops
```

**Via npm (bundled with the full package):**

```bash
npm install @jaguilar87/gaia
```

The `npm install` postinstall hook bootstraps `~/.gaia/gaia.db`, creates the `.claude/` structure via symlinks, and registers the plugin. Run `gaia doctor` to verify, then `gaia scan` to detect your project stack (writes to DB, no `project-context.json` file generated).

## Quick start

```bash
# Verify installation
gaia doctor

# Detect stack and record in ~/.gaia/gaia.db
gaia scan

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

**Agents** (8): `gaia-orchestrator`, `gaia-operator`, `gaia-system`, `gaia-planner`, `developer`, `cloud-troubleshooter`, `gitops-operator`, `platform-architect`

**Skills** (shared library): investigation, security-tiers, command-execution, agent-protocol, gaia-planner, brief-spec, gaia-patterns, fast-queries, subagent-request-approval, execution, orchestrator-present-approval, agent-approval-protocol, readme-writing, skill-creation, memory, and more.

**Hooks** (10 lifecycle events): `PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `SessionStart`, `SubagentStart`, `SubagentStop`, `Stop`, `TaskCompleted`, `PreCompact`, `PostCompact`. The pre-tool-use pipeline enforces command classification (T0-T3) and the nonce-based approval flow.

**Commands**: `/gaia` — namespaced meta-agent for system architecture, agent design, and orchestration debugging.

**CLI tools** (under `bin/`): a single `gaia` binary with subcommands auto-discovered from `bin/cli/` -- `doctor`, `scan`, `status`, `history`, `metrics`, `cleanup`, `uninstall`, `install`, `update`, `context`, `memory`, `approvals`, `plans`, `brief`, `project`.

## Permissions

This plugin requests `Bash(*)` in the allow list — the pre-tool-use hook is the actual security gate. State-changing verbs (create, delete, apply, push, commit) trigger the approval flow; irreversible commands (db drops, cluster deletes, `git push --force`, `mkfs`, `dd`) are permanently denied. Full deny list lives in `settings.json`.

Edit and Write tools are open for normal code paths. Writes to `.claude/hooks/` and `.claude/settings*.json` are hook-protected and require explicit approval regardless of session mode.

## Troubleshooting

- **Symlinks missing after install**: `gaia install` rebuilds them (or re-run `npm install @jaguilar87/gaia`).
- **Multiple Claude Code installations**: `gaia cleanup` removes duplicates.
- **Hook not firing**: `gaia doctor` validates every manifest entry against disk.
- **Full uninstall**: `gaia uninstall --force --remove-all`.

## Links

- Documentation: [github.com/metraton/gaia](https://github.com/metraton/gaia#readme)
- Install guide: [INSTALL.md](https://github.com/metraton/gaia/blob/main/INSTALL.md)
- Issues: [github.com/metraton/gaia/issues](https://github.com/metraton/gaia/issues)
- License: MIT
