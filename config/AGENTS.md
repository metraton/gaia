# AGENTS.md

This repository uses **CLAUDE.md** as the primary orchestrator instruction file for Claude Code.

## For Claude Code Users

See `CLAUDE.md` for complete orchestrator instructions.

**Quick links:**
- Core principles: `CLAUDE.md` (lines 18-36)
- Workflow: `.claude/config/orchestration-workflow.md`
- Git standards: `.claude/config/git-standards.md`
- Agent catalog: `.claude/config/agent-catalog.md`

---

## For Other AI Coding Assistants

This repository is optimized for Claude Code with a specialized agent swarm. Other AI coding assistants may have limited functionality.

### Cursor

If using Cursor, symlink to `.cursor/rules`:

```bash
mkdir -p .cursor
ln -s ../CLAUDE.md .cursor/rules
```

### Cline

If using Cline, symlink to `.clinerules`:

```bash
ln -s CLAUDE.md .clinerules
```

### GitHub Copilot

If using GitHub Copilot, symlink to `.github/copilot-instructions.md`:

```bash
mkdir -p .github
ln -s ../CLAUDE.md .github/copilot-instructions.md
```

---

## Compatibility Notes

**Note:** CLAUDE.md is specifically designed for Claude Code's agent orchestration system. Other AI coding assistants may not support:

- Agent routing via `Task` tool
- Context provisioning via `context_provider.py`
- Approval gates via `AskUserQuestion` tool
- Clarification engine via `clarify_engine.py`
- Multi-phase workflows (Planning → Approval → Realization)

If you're using a different AI coding assistant, treat CLAUDE.md as general guidance rather than executable instructions.

---

## System Architecture

This repository uses a **hierarchical agent system**:

```
Claude Code (Orchestrator)
    ├── terraform-architect (Infrastructure)
    ├── gitops-operator (Kubernetes/Flux)
    ├── cloud-troubleshooter (GCP/AWS diagnostics)
    ├── devops-developer (Application build/test)
    ├── Gaia (System optimization)
    ├── Explore (Codebase exploration)
    └── Plan (Implementation planning)
```

**Orchestrator responsibilities:**
- Route user requests to specialist agents
- Provision context via `context_provider.py`
- Enforce approval gates for T3 operations
- Update system SSOTs (.claude/project-context/project-context.json, tasks.md)
- Handle simple operations directly (ad-hoc commits, queries)

**Agent responsibilities:**
- Execute complex workflows (infrastructure changes, deployments)
- Validate operations (terraform validate, kubectl dry-run)
- Verify in live environment (kubectl get, gcloud describe)
- Report status back to orchestrator

---

## Documentation Structure

```
<project-root>/
├── CLAUDE.md (core orchestrator instructions, 196 lines)
├── AGENTS.md (this file, compatibility layer)
├── .claude/
│   ├── CHANGELOG.md (version history)
│   ├── config/
│   │   ├── orchestration-workflow.md (Phase 0-6 details)
│   │   ├── git-standards.md (commit standards)
│   │   ├── context-contracts.md (agent context requirements)
│   │   └── agent-catalog.md (agent capabilities)
│   ├── agents/ (agent definitions)
│   ├── tools/ (context_provider.py, agent_router.py, etc.)
│   ├── logs/ (audit trail, metrics)
│   ├── tests/ (test suite)
│   └── project-context/
│       └── project-context.json (SSOT for infrastructure state)
```

---

## Quick Start for New Team Members

1. **Read CLAUDE.md** (5 min) - Understand core principles and workflow
2. **Review agent catalog** (10 min) - See available agents and capabilities
3. **Check project context** (5 min) - Review `.claude/project-context/project-context.json` for current infrastructure state
4. **Run sample workflow** (15 min) - Test orchestrator with a simple task

**Sample workflow:**

```bash
# Start Claude Code
claude-code

# Ask orchestrator to route a task
> "Analiza el estado del cluster GKE"

# Orchestrator will:
# 1. Route to cloud-troubleshooter
# 2. Provision context via context_provider.py
# 3. Invoke agent with structured context
# 4. Return diagnostic report
```

---

## Contributing

See `CLAUDE.md` and `.claude/CHANGELOG.md` for contribution guidelines.

**Key rules:**
- All commits MUST follow Conventional Commits (enforced by `commit_validator.py`)
- Changes to infrastructure/deployments require approval gate (Phase 4)
- Update SSOTs after realization (.claude/project-context/project-context.json, tasks.md)

---

## Support

- **Issues:** Create issue in GitHub repository
- **Questions:** Contact Jorge Aguilar (jaguilar@aaxis.com)
- **Documentation:** See `.claude/config/*.md`

---

## License

Internal documentation for Aaxis RnD team. Not for external distribution.
