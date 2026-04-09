# Gaia-Ops Patterns -- Reference

Package: `@jaguilar87/gaia-ops` v4.7.2 | Node >=18 | Python >=3.9

---

## 1. Component Map

### Hook Entry Points (10 files)

| File | Event | Matchers |
|------|-------|----------|
| `hooks/pre_tool_use.py` | PreToolUse | `Bash`, `Task`, `Agent`, `SendMessage`, `Read\|Edit\|Write\|Glob\|Grep\|WebSearch\|WebFetch\|NotebookEdit` |
| `hooks/post_tool_use.py` | PostToolUse | `Bash`, `AskUserQuestion` |
| `hooks/stop_hook.py` | Stop | (all) |
| `hooks/user_prompt_submit.py` | UserPromptSubmit | (all) |
| `hooks/subagent_start.py` | SubagentStart | `*` |
| `hooks/subagent_stop.py` | SubagentStop | `*` |
| `hooks/session_start.py` | SessionStart | `startup` |
| `hooks/task_completed.py` | TaskCompleted | (all) |
| `hooks/post_compact.py` | PostCompact | (all) |
| `hooks/elicitation_result.py` | ElicitationResult | (none registered) |

### Hook Modules (13 packages)

| Package | Files | Purpose |
|---------|-------|---------|
| `core/` | `hook_entry`, `paths`, `plugin_mode`, `plugin_setup`, `state`, `stdin` | Entry dispatch, path resolution, mode detection, shared state |
| `security/` | `blocked_commands`, `mutative_verbs`, `tiers`, `gitops_validator`, `command_semantics`, `approval_grants`, `approval_scopes`, `approval_cleanup`, `approval_constants`, `approval_messages`, `blocked_message_formatter`, `prompt_validator` | T3 gate, blocked commands, approval nonce lifecycle |
| `audit/` | `logger`, `metrics`, `event_detector`, `workflow_auditor`, `workflow_recorder` | Structured logging, metrics collection, workflow audit trail |
| `tools/` | `bash_validator`, `cloud_pipe_validator`, `shell_parser`, `task_validator`, `hook_response` | Command validation, pipe detection, shell parsing |
| `context/` | `context_injector`, `context_writer`, `context_freshness`, `contracts_loader`, `compact_context_builder`, `anchor_tracker` | Project-context injection, freshness checks, contract loading |
| `agents/` | `contract_validator`, `response_contract`, `skill_injection_verifier`, `task_info_builder`, `transcript_analyzer`, `transcript_reader` | json:contract validation, skill verification, transcript analysis |
| `session/` | `session_manager`, `session_context_writer`, `session_event_injector` | Session lifecycle, context persistence |
| `orchestrator/` | `delegate_mode` | Delegation mode detection |
| `validation/` | `commit_validator` | Git commit validation |
| `scanning/` | `scan_trigger` | Auto-scan trigger |
| `events/` | `event_writer` | Structured event output |
| `memory/` | `episode_writer` | Episodic memory persistence |
| `adapters/` | `base`, `channel`, `claude_code`, `types`, `utils` | Hook I/O abstraction layer |

### Agents (8)

| Agent | File | Domain |
|-------|------|--------|
| gaia-orchestrator | `agents/gaia-orchestrator.md` | Routes requests, manages workflow, consolidation |
| gaia-operator | `agents/gaia-operator.md` | CI/CD, pipelines, release automation |
| gaia-system | `agents/gaia-system.md` | Gaia-ops meta-system itself |
| developer | `agents/developer.md` | Application code (Node/TS, Python) |
| cloud-troubleshooter | `agents/cloud-troubleshooter.md` | Live cloud diagnostics |
| gitops-operator | `agents/gitops-operator.md` | Kubernetes, HelmRelease, Flux |
| terraform-architect | `agents/terraform-architect.md` | Terraform/Terragrunt IaC |
| speckit-planner | `agents/speckit-planner.md` | Feature specification and planning |

### Skills (20 directories + 1 top-level reference)

| Skill | Type | Injection |
|-------|------|-----------|
| `agent-protocol/` | Protocol | Injected (all agents) |
| `agent-response/` | Technique | Injected (orchestrator) |
| `approval/` | Technique | On-demand |
| `command-execution/` | Discipline | Injected |
| `context-updater/` | Technique | Injected |
| `developer-patterns/` | Domain | Injected (developer) |
| `execution/` | Discipline | On-demand |
| `fast-queries/` | Technique | Injected |
| `gaia-patterns/` | Domain | Injected (gaia-system) |
| `git-conventions/` | Reference | On-demand |
| `gitops-patterns/` | Domain | Injected (gitops-operator) |
| `gmail-policy/` | Domain | Injected (orchestrator) |
| `investigation/` | Technique | Injected |
| `memory-management/` | Technique | Injected (orchestrator) |
| `orchestrator-approval/` | Technique | Injected (orchestrator) |
| `security-tiers/` | Reference | Injected (all agents) |
| `skill-creation/` | Technique | Injected (gaia-system) |
| `specification/` | Technique | Injected (speckit-planner) |
| `speckit-workflow/` | Domain | Injected (speckit-planner) |
| `terraform-patterns/` | Domain | Injected (terraform-architect) |
| `skills/reference.md` | Reference | On-demand (shared security-tiers ref) |

### Commands (slash commands)

| Command | File | Purpose |
|---------|------|---------|
| `/gaia` | `commands/gaia.md` | Invoke gaia meta-agent |
| `/scan-project` | `commands/scan-project.md` | Scan project, generate project-context.json |
| `/speckit.init` | `commands/speckit.init.md` | Bootstrap Spec-Kit structure |
| `/speckit.plan` | `commands/speckit.plan.md` | Implementation planning |
| `/speckit.tasks` | `commands/speckit.tasks.md` | Task generation from spec |
| `/speckit.add-task` | `commands/speckit.add-task.md` | Add task to existing plan |
| `/speckit.analyze-task` | `commands/speckit.analyze-task.md` | Analyze specific task |

### Tools (7 subsystems)

| Subsystem | Location | Purpose |
|-----------|----------|---------|
| context | `tools/context/` | `context_provider`, `context_section_reader`, `deep_merge`, `pending_updates`, `surface_router` |
| fast-queries | `tools/fast-queries/` | Triage scripts for cloud/gitops/terraform/appservices |
| gaia_simulator | `tools/gaia_simulator/` | Routing simulator: `cli`, `extractor`, `reporter`, `routing_simulator`, `runner`, `skills_mapper` |
| memory | `tools/memory/` | `episodic` -- episodic memory store |
| review | `tools/review/` | `review_engine` -- code review engine |
| scan | `tools/scan/` | Project scanner: `orchestrator`, `registry`, `scanners/`, `config`, `merge`, `verify`, `walk`, `workspace`, `ui` |
| validation | `tools/validation/` | `approval_gate`, `validate_skills` |
| (top-level) | `tools/persist_transcript_analysis.py` | Transcript persistence utility |

### CLI Tools (10 bin commands + 1 wrapper)

| Command | File | Purpose |
|---------|------|---------|
| `gaia-doctor` | `bin/gaia-doctor.js` | Health check: hooks, symlinks, Python, config |
| `gaia-skills-diagnose` | `bin/gaia-skills-diagnose.js` | Skills injection diagnostics |
| `gaia-cleanup` | `bin/gaia-cleanup.js` | Remove symlinks and settings (preuninstall) |
| `gaia-uninstall` | `bin/gaia-uninstall.js` | Full uninstall |
| `gaia-metrics` | `bin/gaia-metrics.js` | Usage metrics and analytics |
| `gaia-review` | `bin/gaia-review.js` | Code review utility |
| `gaia-status` | `bin/gaia-status.js` | Installation status report |
| `gaia-history` | `bin/gaia-history.js` | Session history viewer |
| `gaia-update` | `bin/gaia-update.js` | Postinstall: symlinks, settings merge, verification |
| `gaia-scan` | `bin/gaia-scan` | Shell wrapper for `gaia-scan.py` |
| `gaia-scan.py` | `bin/gaia-scan.py` | Project scanner (Python implementation) |
| `pre-publish-validate` | `bin/pre-publish-validate.js` | Pre-publish validation (not a bin export) |

### Config Files

| File | Purpose |
|------|---------|
| `config/universal-rules.json` | Rules shared by both plugin modes |
| `config/context-contracts.json` | Context injection contracts per agent |
| `config/surface-routing.json` | Surface routing table (intent to agent mapping) |
| `config/git_standards.json` | Git commit and branch standards |
| `config/cloud/aws.json` | AWS service patterns and commands |
| `config/cloud/gcp.json` | GCP service patterns and commands |

---

## 2. Plugin Modes

| Mode | Package | What ships |
|------|---------|-----------|
| `gaia-ops` | `@jaguilar87/gaia-ops` | All hooks, all modules, all agents, all skills, all commands, all tools, all config |
| `gaia-security` | `@jaguilar87/gaia-security` | 5 hooks (`pre_tool_use`, `post_tool_use`, `stop_hook`, `user_prompt_submit`, `session_start`), all modules, no agents, no skills, `config/universal-rules.json` only |

### Detection Cascade (`hooks/modules/core/plugin_mode.py`)

```
1. plugin-registry.json    -- checks installed[].name for "gaia-ops" or "gaia-security"
2. CLAUDE_PLUGIN_ROOT + plugin.json  -- reads .claude-plugin/plugin.json name field
3. NPM package path        -- inspects node_modules path for package name
4. GAIA_PLUGIN_MODE env    -- explicit override ("security" or "ops")
5. Default: "security"     -- most restrictive fallback
```

### Mode Behavioral Differences

| Behavior | `security` mode | `ops` mode |
|----------|----------------|------------|
| T3 approval | Claude Code native dialog (`permissionDecision: ask`) | Hook blocks with nonce, orchestrator approval flow |
| Agents | None | 8 agents routed by orchestrator |
| Skills | None | 20 skills injected per frontmatter |
| Commands | None | 7 slash commands |
| PreToolUse matchers | `Bash` only | `Bash`, `Task`, `Agent`, `SendMessage`, multi-tool |

---

## 3. Build / Publish Pipeline

### Build

```
scripts/build-plugin.py <plugin-name> [--output-dir <path>]
```

1. Reads `build/<plugin-name>.manifest.json`
2. Resolves `"all"` to concrete file lists
3. Copies to `dist/<plugin-name>/`
4. Generates `hooks.json` and `settings.json` from manifest

### Publish

```
npm run build:plugins          # builds both gaia-security + gaia-ops to dist/
npm run pre-publish:validate   # validates dist/ contents
npm run prepublishOnly         # build + validate (automatic before npm publish)
npm publish                    # publishes @jaguilar87/gaia-ops
```

### Postinstall (`bin/gaia-update.js`, runs on `npm install`)

**First install** (no `.claude/`):
1. Check Python 3 available
2. Run `gaia-scan --npm-postinstall` to create `.claude/`, symlinks, settings, project-context
3. Create `plugin-registry.json`
4. Merge permissions into `settings.local.json`
5. Merge hooks into `settings.local.json`
6. Verification

**Update** (`.claude/` exists):
1. Show version transition
2. `settings.json`: create only if missing (non-invasive)
3. Merge permissions, env vars, agent key into `settings.local.json` (union, preserves user config)
4. Merge hooks from `hooks.json` into `settings.local.json`
5. Recreate/fix broken symlinks
6. Verify hooks, Python, project-context, config

### Symlinks Created

```
.claude/agents   -> node_modules/@jaguilar87/gaia-ops/agents/
.claude/hooks    -> node_modules/@jaguilar87/gaia-ops/hooks/
.claude/skills   -> node_modules/@jaguilar87/gaia-ops/skills/
.claude/tools    -> node_modules/@jaguilar87/gaia-ops/tools/
.claude/commands -> node_modules/@jaguilar87/gaia-ops/commands/
.claude/config   -> node_modules/@jaguilar87/gaia-ops/config/
```

---

## 4. Test Pyramid

### Layers

| Layer | Command | Cost | Speed | Count |
|-------|---------|------|-------|-------|
| L1 | `npm test` | Free | ~0.25s | ~1462 |
| L2 | `npm run test:layer2` | ~$0.10 | Minutes | ~11 |
| L3 | `npm run test:layer3` | Free | Minutes | ~13 |

### L1 Categories (46 test files)

| Category | Directory | What it tests |
|----------|-----------|---------------|
| Prompt regression | `tests/layer1_prompt_regression/` | Routing table, skill content rules, agent frontmatter, agent prompts, security tier consistency, skills cross-reference, context contracts |
| Hooks | `tests/hooks/modules/` | Security modules (mutative_verbs, blocked_commands, tiers, gitops_validator, approval_grants, approval_scopes, command_semantics), tools (bash_validator, shell_parser, cloud_pipe_validator, task_validator), core (paths, state), context (context_writer) |
| System | `tests/system/` | Directory structure, permissions, agent definitions, configuration, schema compatibility |
| Tools | `tests/tools/` | context_provider, episodic, pending_updates, deep_merge, review_engine, surface_router |
| Integration | `tests/integration/` | Context enrichment, subagent lifecycle, subagent stop, nonce approval relay |
| Performance | `tests/performance/` | Context injection benchmarks |
| Cross-layer | `tests/test_cross_layer_consistency.py` | Consistency between hooks, config, and agents |

### L2 (LLM Evaluation)

| File | What it tests |
|------|---------------|
| `tests/layer2_llm_evaluation/test_agent_behavior.py` | Agent response quality via LLM judge |

### L3 (End-to-End)

| File | What it tests |
|------|---------------|
| `tests/layer3_e2e/test_installation_smoke.py` | npm install in /tmp/, symlinks, settings, hooks |
| `tests/layer3_e2e/test_hook_lifecycle.py` | Full hook lifecycle: pre/post tool use, session |

### Which Tests for Which Changes

| Change | Run |
|--------|-----|
| Hook module (security, tools, core) | `pytest tests/hooks/ -v` |
| Agent definition (.md) | `pytest tests/layer1_prompt_regression/ tests/system/ -v` |
| Skill content | `pytest tests/layer1_prompt_regression/ -v` |
| Config file | `pytest tests/system/ tests/test_cross_layer_consistency.py -v` |
| Context/routing | `pytest tests/tools/ tests/integration/ -v` |
| CLI tool (bin/) | `pytest tests/layer3_e2e/ -v -m e2e` |
| Any change (pre-commit) | `npm test` (full L1) |
| Pre-publish | `npm run build:plugins && npm run pre-publish:validate` |

---

## 5. CLI Tools

| Command | Purpose | When to use |
|---------|---------|-------------|
| `npx gaia-doctor` | Health check: hooks reachable, symlinks valid, Python available, config present | After install, after update, debugging |
| `npx gaia-skills-diagnose` | Skills injection: verifies frontmatter, SKILL.md presence, injection pipeline | Skills not loading, wrong skills in agent |
| `npx gaia-status` | Installation status: version, mode, symlinks, settings | Quick status check |
| `npx gaia-metrics` | Usage analytics: hook invocations, tier distribution, approval rates | Performance analysis |
| `npx gaia-review` | Code review utility | PR review |
| `npx gaia-history` | Session history viewer | Debugging past sessions |
| `npx gaia-update` | Re-run postinstall: fix symlinks, merge settings | Manual repair |
| `npx gaia-cleanup` | Remove symlinks and settings (runs on preuninstall) | Before uninstall |
| `npx gaia-uninstall` | Full uninstall: cleanup + remove package artifacts | Complete removal |
| `npx gaia-scan` | Project scanner: detect stack, generate project-context.json | New project setup |
| `node bin/pre-publish-validate.js` | Validate dist/ before npm publish | Release workflow |

---

## 6. Dev Workflow

### Dev Mode (symlinks to source)

```bash
# In any project directory:
ln -sf /home/jorge/ws/me/gaia-ops-dev/agents   .claude/agents
ln -sf /home/jorge/ws/me/gaia-ops-dev/hooks    .claude/hooks
ln -sf /home/jorge/ws/me/gaia-ops-dev/skills   .claude/skills
ln -sf /home/jorge/ws/me/gaia-ops-dev/tools    .claude/tools
ln -sf /home/jorge/ws/me/gaia-ops-dev/commands .claude/commands
ln -sf /home/jorge/ws/me/gaia-ops-dev/config   .claude/config
```

Changes to source files take effect immediately (no build step).

### Release Mode (npm install)

```bash
npm install @jaguilar87/gaia-ops
# postinstall creates symlinks: .claude/* -> node_modules/@jaguilar87/gaia-ops/*
```

### Test Isolation

```bash
cd /tmp
mkdir test-project && cd test-project
npm init -y
npm install ~/ws/me/gaia-ops-dev    # installs from local source
npx gaia-doctor                      # verify installation
npm test                             # run L1 suite from gaia-ops-dev
```

### Version Bump + Publish

```bash
npm version patch|minor|major        # bump in package.json
npm run build:plugins                 # rebuild dist/
npm run pre-publish:validate          # validate
npm publish                           # publish to npm
```

---

## 7. Validation Tools

### Routing Simulator

```bash
python3 tools/gaia_simulator/cli.py "deploy the terraform changes"
```

Tests the surface-routing pipeline: prompt -> intent extraction -> agent selection. Validates that `config/surface-routing.json` routes correctly without invoking any agent.

Components: `cli.py` (entry), `routing_simulator.py` (engine), `extractor.py` (intent), `skills_mapper.py` (skill resolution), `runner.py` (batch), `reporter.py` (output).

### Transcript Analyzer

```bash
# Within hooks, automatically invoked by subagent_stop
hooks/modules/agents/transcript_analyzer.py
```

Analyzes agent transcripts for contract compliance, skill adherence, and behavioral patterns. Used by `subagent_stop.py` to validate agent output. Paired with `transcript_reader.py` for parsing.

### Skills Diagnostics

```bash
npx gaia-skills-diagnose
```

Validates the full skills pipeline: frontmatter declarations, SKILL.md file presence, injection chain integrity, on-demand vs injected classification.

### Approval Gate

```bash
python3 tools/validation/approval_gate.py
```

Validates T3 approval nonce lifecycle: generation, scope matching, expiry, grant/deny.

### Doctor

```bash
npx gaia-doctor
```

Full system health: hook reachability (all 10 entry points), symlink integrity, Python environment, config file presence, settings.json/settings.local.json correctness.
