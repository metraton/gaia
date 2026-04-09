# Gaia-Ops Product Map

`@jaguilar87/gaia-ops` v4.7.2 | Node >=18 | Python >=3.9

---

## Installation Modes

| Mode | Package | What Ships |
|------|---------|------------|
| `gaia-ops` | `npm install @jaguilar87/gaia-ops` | All hooks, agents, skills, commands, tools, config |
| `gaia-security` | `npm install @jaguilar87/gaia-security` | 5 hooks, security modules only. No agents, no skills |

Both modes auto-configure on `npm install` via postinstall hook.

---

## CLI Tools

All available via `npx <command>` after install.

| Command | Purpose |
|---------|---------|
| `gaia-doctor` | Health check: hooks, symlinks, Python, config |
| `gaia-skills-diagnose` | Skills injection diagnostics and contract gaps |
| `gaia-status` | Installation status report (version, mode, symlinks) |
| `gaia-metrics` | Usage analytics: hook invocations, tier distribution |
| `gaia-scan` | Project scanner: detect stack, generate project-context.json |
| `gaia-update` | Re-run postinstall: fix symlinks, merge settings |
| `gaia-review` | Code review utility |
| `gaia-history` | Session history viewer |
| `gaia-cleanup` | Remove symlinks and settings (preuninstall) |
| `gaia-uninstall` | Full removal of package artifacts |
| `pre-publish-validate` | Validate dist/ before npm publish (internal) |

---

## Test Layers

| Layer | Command | Cost | Speed | Count |
|-------|---------|------|-------|-------|
| L1 | `npm test` | Free | ~0.25s | ~1462 |
| L2 | `npm run test:layer2` | ~$0.10 | Minutes | ~11 |
| L3 | `npm run test:layer3` | Free | Minutes | ~13 |

### L1 Categories

| Category | Directory | What It Tests |
|----------|-----------|---------------|
| Prompt regression | `tests/layer1_prompt_regression/` | Routing, skills, agent frontmatter, security tiers |
| Hooks | `tests/hooks/modules/` | Security, tools, core, context modules |
| System | `tests/system/` | Structure, permissions, agents, config, schema |
| Tools | `tests/tools/` | Context, episodic, pending updates, review, router |
| Integration | `tests/integration/` | Context enrichment, subagent lifecycle, nonce relay |
| Performance | `tests/performance/` | Context injection benchmarks |
| Cross-layer | `tests/test_cross_layer_consistency.py` | Hook/config/agent consistency |

### Which Tests for Which Changes

| Change | Run |
|--------|-----|
| Hook module | `pytest tests/hooks/ -v` |
| Agent definition | `pytest tests/layer1_prompt_regression/ tests/system/ -v` |
| Skill content | `pytest tests/layer1_prompt_regression/ -v` |
| Config file | `pytest tests/system/ tests/test_cross_layer_consistency.py -v` |
| Context/routing | `pytest tests/tools/ tests/integration/ -v` |
| CLI tool | `pytest tests/layer3_e2e/ -v -m e2e` |
| Pre-publish | `npm run build:plugins && npm run pre-publish:validate` |

---

## Metrics and Anomaly Detection

| Tool | What It Tracks |
|------|----------------|
| `hooks/modules/audit/metrics.py` | Hook invocations, tier distribution, approval rates |
| `hooks/modules/audit/event_detector.py` | Anomalous patterns in agent behavior |
| `hooks/modules/audit/workflow_auditor.py` | Workflow compliance and audit trail |
| `npx gaia-metrics` | CLI access to collected metrics |

---

## Dev Workflow

### Dev Mode (live source edits)

```bash
# Symlink .claude/ to local source -- changes take effect immediately
ln -sf /path/to/gaia-ops-dev/agents   .claude/agents
ln -sf /path/to/gaia-ops-dev/hooks    .claude/hooks
ln -sf /path/to/gaia-ops-dev/skills   .claude/skills
ln -sf /path/to/gaia-ops-dev/tools    .claude/tools
ln -sf /path/to/gaia-ops-dev/commands .claude/commands
ln -sf /path/to/gaia-ops-dev/config   .claude/config
```

### Release Mode (npm install)

```bash
npm install @jaguilar87/gaia-ops  # postinstall creates .claude/ symlinks
```

### Version Bump and Publish

```bash
npm version patch|minor|major
npm run build:plugins
npm run pre-publish:validate
npm publish
```

### Test Isolation

```bash
cd /tmp && mkdir test-project && cd test-project
npm init -y
npm install ~/ws/me/gaia-ops-dev    # install from local source
npx gaia-doctor                      # verify
```

---

## Agent Roster

| Agent | Domain |
|-------|--------|
| `gaia-orchestrator` | Routes requests, manages workflow, consolidation loop |
| `gaia-operator` | CI/CD pipelines, release automation |
| `gaia-system` | Gaia-ops meta-system itself (agents, skills, hooks) |
| `developer` | Application code (Node/TS, Python) |
| `cloud-troubleshooter` | Live cloud diagnostics (GCP, AWS) |
| `gitops-operator` | Kubernetes, HelmRelease, Flux GitOps |
| `terraform-architect` | Terraform/Terragrunt infrastructure as code |
| `speckit-planner` | Feature specification and planning |

---

## Skill Roster

| Skill | Type | Injection |
|-------|------|-----------|
| `agent-protocol` | Protocol | Injected (all agents) |
| `agent-response` | Technique | Injected (orchestrator) |
| `approval` | Technique | On-demand |
| `command-execution` | Discipline | Injected |
| `context-updater` | Technique | Injected |
| `developer-patterns` | Domain | Injected (developer) |
| `execution` | Discipline | On-demand |
| `fast-queries` | Technique | Injected |
| `gaia-patterns` | Domain | Injected (gaia-system) |
| `git-conventions` | Reference | On-demand |
| `gitops-patterns` | Domain | Injected (gitops-operator) |
| `gmail-policy` | Domain | Injected (orchestrator) |
| `investigation` | Technique | Injected |
| `memory-management` | Technique | Injected (orchestrator) |
| `orchestrator-approval` | Technique | Injected (orchestrator) |
| `security-tiers` | Reference | Injected (all agents) |
| `skill-creation` | Technique | Injected (gaia-system) |
| `specification` | Technique | Injected (speckit-planner) |
| `speckit-workflow` | Domain | Injected (speckit-planner) |
| `terraform-patterns` | Domain | Injected (terraform-architect) |

---

## Slash Commands

| Command | Purpose |
|---------|---------|
| `/gaia` | Invoke gaia meta-agent |
| `/scan-project` | Scan project, generate project-context.json |
| `/speckit.init` | Bootstrap Spec-Kit structure |
| `/speckit.plan` | Implementation planning |
| `/speckit.tasks` | Task generation from spec |
| `/speckit.add-task` | Add task to existing plan |
| `/speckit.analyze-task` | Analyze specific task |

---

## Security Tiers

| Tier | Name | Side Effects | Approval |
|------|------|-------------|----------|
| T0 | Read-Only | None | No |
| T1 | Validation | None (local) | No |
| T2 | Simulation | None (dry-run) | No |
| T3 | Realization | Modifies state | Yes |

Enforcement: `hooks/modules/security/blocked_commands.py` (permanent deny) + `hooks/modules/security/mutative_verbs.py` (nonce-based approval). Everything not blocked and not mutative is safe by elimination.

---

## References

- [INSTALL.md](../INSTALL.md) -- Installation guide
- [ARCHITECTURE.md](../ARCHITECTURE.md) -- System architecture
- [CONTRIBUTING.md](../CONTRIBUTING.md) -- Contributing guide
- [skills/gaia-patterns/reference.md](../skills/gaia-patterns/reference.md) -- Full component map
