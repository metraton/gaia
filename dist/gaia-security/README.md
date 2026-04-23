# gaia-security

Keeps you in the loop only when it matters. Gaia Security analyzes every Bash command and classifies it into four risk tiers: read-only runs freely, validation and simulation pass through, state-changing operations (create, delete, apply, push) pause for explicit approval, and irreversible commands (database drops, cluster deletes, `git push --force`, `mkfs`, `dd`) are permanently blocked.

Install this plugin when you want Gaia's security pipeline without the agent roster, skills, or orchestrator. It is the stripped-down path — hooks, modules, and the deny list. If you want the full Gaia experience with eight specialist agents and a planner, install `gaia-ops` instead.

## Install

**Via Claude Code marketplace:**

```
/plugin marketplace add metraton/gaia
/plugin install gaia-security
```

**Via npm (standalone dist):**

```bash
npm install @jaguilar87/gaia
# Then point Claude Code at dist/gaia-security/ in your settings.
```

## Quick start

Once installed, the hooks activate automatically on session start. Try any of these to see the pipeline in action:

```bash
# Safe (T0) — runs directly
ls -la

# Validation (T1) — runs directly
terraform validate

# Simulation (T2) — runs directly
terraform plan

# Mutative (T3) — prompts for approval
terraform apply

# Blocked — permanently denied, no prompt
gcloud sql instances delete my-prod-db
```

To manage approvals during a session:

```
# Inside Claude Code
aprobar            # show pending approvals
approve P-<id>     # grant a pending request
reject P-<id>      # deny a pending request
```

## What ships with this plugin

**Hooks** (5 lifecycle events): `PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `SessionStart`, `Stop`. All wired to the same security pipeline that powers the full gaia-ops plugin.

**Hook modules** (under `hooks/modules/`):

- `core/` — classification logic, tier assignment
- `security/` — blocked commands, mutative verb detection, nonce issuance
- `audit/` — session registry, approval persistence
- `tools/` — Bash, Edit, Write adapters
- `validation/` — schema checks, permission validation
- `identity/`, `context/`, `scanning/`, `session/`, `memory/`, `orchestrator/`, `events/` — supporting concerns
- `adapters/claude_code.py` — bridges Gaia classification to CC native permission model

**No agents. No skills. No commands.** This plugin is hooks + deny list by design.

**Config**: `config/universal-rules.json` — shared rule set that governs tier classification.

## Permissions

- `Bash(*)` allowed — the pre-tool-use hook is the real security gate.
- `Read`, `Glob`, `Grep`, `BashOutput`, `KillShell` allowed for inspection.
- 45 irreversible commands permanently denied in `settings.json` (AWS VPC/RDS/S3 deletes, GCP project/cluster/SQL deletes, Kubernetes namespace/node/PV deletes, `git push --force`, `dd`, `mkfs`).

Writes to `.claude/hooks/` and `.claude/settings*.json` are hook-protected — approval required even in `bypassPermissions` mode.

## Troubleshooting

- **Hook not firing**: confirm `hooks/hooks.json` is present and every `entries` file resolves on disk. Missing files are silently skipped.
- **Approval flow stuck**: check `~/.claude/logs/` for hook traces; `aprobar` lists pending requests.
- **Want the full system**: install `gaia-ops` — same hook pipeline, plus eight agents, skills, and the orchestrator.

## Links

- Documentation: [github.com/metraton/gaia#gaia-security](https://github.com/metraton/gaia#gaia-security)
- Security policy: [SECURITY.md](https://github.com/metraton/gaia/blob/main/SECURITY.md)
- Issues: [github.com/metraton/gaia/issues](https://github.com/metraton/gaia/issues)
- License: MIT
