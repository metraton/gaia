# Gaia Patterns -- Reference

Package: `@jaguilar87/gaia` v5.0.0-rc1 | Node >=18 | Python >=3.9

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
| `hooks/session_end_hook.py` | SessionEnd | (all) |
| `hooks/task_completed.py` | TaskCompleted | (all) |
| `hooks/post_compact.py` | PostCompact | (all) |
| `hooks/elicitation_result.py` | ElicitationResult | (all) |

SessionStart emits a one-shot `hookSpecificOutput.additionalContext` manifest (Environment, Active Agentic Loop, [ACTIONABLE] pending approvals) when running in ops mode. UserPromptSubmit injects per-turn signals only: deterministic `## Surface Routing Recommendation` and a first-run welcome. SubagentStart injects two memory blocks into every dispatched agent: `## Memory Index` (episodic atoms ranked by relevance to the task) and `## Workspace Memory` (curated persistent memory atoms — user preferences, key decisions, project facts — stored in `~/.gaia/gaia.db` and scoped to the workspace).

### Hook Modules (13 packages)

| Package | Files | Purpose |
|---------|-------|---------|
| `core/` | `hook_entry`, `paths`, `plugin_mode`, `plugin_setup`, `state`, `stdin` | Entry dispatch, path resolution, mode detection, shared state |
| `security/` | `blocked_commands`, `mutative_verbs`, `tiers`, `command_semantics`, `approval_grants`, `approval_scopes`, `approval_cleanup`, `approval_constants`, `approval_messages`, `blocked_message_formatter`, `prompt_validator` | T3 gate, blocked commands, approval nonce lifecycle |
| `audit/` | `logger`, `metrics`, `event_detector`, `workflow_auditor`, `workflow_recorder` | Structured logging, metrics collection, workflow audit trail |
| `tools/` | `bash_validator`, `cloud_pipe_validator`, `shell_parser`, `task_validator`, `hook_response` | Command validation, pipe detection, shell parsing |
| `context/` | `context_injector`, `context_writer`, `context_freshness`, `contracts_loader`, `compact_context_builder`, `anchor_tracker` | Project-context injection, freshness checks, contract loading |
| `agents/` | `contract_validator`, `response_contract`, `skill_injection_verifier`, `task_info_builder`, `transcript_analyzer`, `transcript_reader` | agent_contract_handoff validation, skill verification, transcript analysis |
| `session/` | `session_manager`, `session_context_writer`, `session_event_injector`, `session_registry`, `session_manifest`, `pending_scanner` | Session lifecycle, heartbeat-based liveness registry, SessionStart manifest builders, pending-approval scanner |
| `orchestrator/` | `delegate_mode` | Delegation mode detection |
| `validation/` | `commit_validator` | Git commit validation |
| `scanning/` | `scan_trigger` | Auto-scan trigger |
| `events/` | `event_writer` | Structured event output |
| `memory/` | `episode_writer` | Episodic memory persistence |
| `adapters/` | `base`, `channel`, `claude_code`, `types`, `utils` | Hook I/O abstraction layer |

### Agents (8)

| Agent | File | Domain | permissionMode |
|-------|------|--------|----------------|
| gaia-orchestrator | `agents/gaia-orchestrator.md` | Routes requests, manages workflow, consolidation | (not set) |
| gaia-operator | `agents/gaia-operator.md` | Workspace operator -- personal workspace tasks, memory management, integrations | `acceptEdits` |
| gaia-system | `agents/gaia-system.md` | Gaia-ops meta-system itself | `acceptEdits` |
| developer | `agents/developer.md` | Application code (Node/TS, Python) | `acceptEdits` |
| cloud-troubleshooter | `agents/cloud-troubleshooter.md` | Live cloud diagnostics | (not set) |
| gitops-operator | `agents/gitops-operator.md` | Kubernetes, HelmRelease, Flux | `acceptEdits` |
| platform-architect | `agents/platform-architect.md` | Terraform/Terragrunt IaC | `acceptEdits` |
| gaia-planner | `agents/gaia-planner.md` | Feature planning, briefs, and task decomposition | `acceptEdits` |

### Skills (21 directories + 1 top-level reference)

| Skill | Type | Injection |
|-------|------|-----------|
| `agent-contract-handoff/` | Reference | On-demand |
| `agent-protocol/` | Protocol | Injected (all agents) |
| `agent-response/` | Protocol | Injected (orchestrator) |
| `approval/` | Technique | On-demand |
| `blog-writing/` | Technique | Injected (gaia-operator) |
| `command-execution/` | Discipline | Injected |
| `execution/` | Discipline | On-demand |
| `fast-queries/` | Reference | Injected |
| `gaia-patterns/` | Domain | Injected (gaia-system) |
| `gaia-release/` | Technique | Injected (gaia-system) |
| `git-conventions/` | Reference | On-demand |
| `gmail-policy/` | Reference | Injected (orchestrator) |
| `gmail-triage/` | Technique | Injected (gaia-operator) |
| `gws-setup/` | Technique | On-demand |
| `investigation/` | Technique | Injected |
| `memory/` | Technique | Injected (gaia-operator) |
| `orchestrator-present-approval/` | Discipline | Injected (orchestrator) |
| `security-tiers/` | Reference | Injected (all agents) |
| `skill-creation/` | Technique | Injected (gaia-system) |
| `skills/reference.md` | Reference | On-demand (shared security-tiers ref) |

### Tools (7 subsystems)

| Subsystem | Location | Purpose |
|-----------|----------|---------|
| context | `tools/context/` | `context_provider`, `deep_merge`, `surface_router` |
| fast-queries | `tools/fast-queries/` | Triage scripts for cloud/gitops/terraform/appservices |
| gaia_simulator | `tools/gaia_simulator/` | Routing simulator: `cli`, `extractor`, `reporter`, `routing_simulator`, `runner`, `skills_mapper` |
| memory | `tools/memory/` | `episodic` -- episodic memory store |
| review | `tools/review/` | (deprecated; `review_engine` removed -- review logic lives in skills/code-review) |
| scan | `tools/scan/` | Project scanner: `core` (single nucleus -- all entry points call `scan_workspace`), `orchestrator`, `registry`, `scanners/`, `config`, `merge`, `verify`, `walk`, `workspace`, `ui` |
| validation | `tools/validation/` | `approval_gate`, `validate_skills` |
| (top-level) | `tools/persist_transcript_analysis.py` | Transcript persistence utility |

### CLI Tools

The package ships a single `gaia` binary (`bin/gaia.js`) that dispatches to Python subcommands discovered under `bin/cli/<name>.py`. Each subcommand is a self-contained module loaded by name.

| Subcommand | File | Purpose |
|------------|------|---------|
| `gaia approvals` | `bin/cli/approvals.py` | Approval system v2: list, show, accept, reject pending T3 grants |
| `gaia brief` | `bin/cli/brief.py` | Brief CRUD against the Gaia DB substrate (new, edit, show, status, close) |
| `gaia cleanup` | `bin/cli/cleanup.py` | Remove temporary caches, old logs, and `__pycache__`; preserves .claude/ symlinks and ~/.gaia/gaia.db |
| `gaia context` | `bin/cli/context.py` | Display, refresh, and inspect project context (legacy JSON + DB-backed sections) |
| `gaia doctor` | `bin/cli/doctor.py` | System health checks: schema, FTS5 sync, agent_permissions, symlinks, settings.local.json |
| `gaia history` | `bin/cli/history.py` | Recent agent sessions: list, show, search |
| `gaia install` | `bin/cli/install.py` | Bootstrap DB + .claude/ structure + symlinks for a fresh install (no npm postinstall -- run manually or via lazy bootstrap on first `gaia` use) |
| `gaia memory` | `bin/cli/memory.py` | Episodic memory: FTS5 search, show episode, health checks |
| `gaia metrics` | `bin/cli/metrics.py` | Usage analytics: tier classification, agent invocations, anomaly counters |
| `gaia paths` | `bin/cli/paths.py` | Inspect canonical Gaia storage paths (DB, plugin root, workspace) |
| `gaia plan` | `bin/cli/plan.py` | Manage plans (one per brief, DB-canonical): save, show, list, status |
| `gaia workspace` | `bin/cli/workspace.py` | Workspace identity and consolidate operations |
| `gaia scan` | `bin/cli/scan.py` | In-process project scan: detect stack, sync results to ~/.gaia/gaia.db (DB-canonical; no project-context.json written) |
| `gaia status` | `bin/cli/status.py` | Quick installation snapshot: version, mode, DB path, registered workspace, last scan |
| `gaia uninstall` | `bin/cli/uninstall.py` | Disconnect Gaia from the current workspace (wraps cleanup + preuninstall mode) |
| `gaia update` | `bin/cli/update.py` | Refresh DB schema, .claude/ config, and symlinks after a package upgrade |

### Config Files

| File | Purpose |
|------|---------|
| `config/context-contracts.json` | Seeding source for per-agent context contracts (applied to gaia.db on install; runtime SSOT is DB) |
| `config/surface-routing.json` | Surface routing table (intent to agent mapping) |
| `config/cloud/aws.json` | AWS service patterns and commands |
| `config/cloud/gcp.json` | GCP service patterns and commands |

---

## 2. Plugin Modes

Gaia ships as a **single** npm package (`@jaguilar87/gaia`) and a **single** Claude Code plugin (`gaia`, built to `dist/gaia`). The former `gaia-ops` / `gaia-security` package split is retired -- one bundle carries all hooks, modules, agents, skills, tools, and config.

Internally, the hook layer still recognizes two *behavioral* modes via `hooks/modules/core/plugin_mode.py`. This is not a packaging split -- it is a runtime fallback that keeps pre-existing installs (registries written before the rename, or anyone still exporting `GAIA_PLUGIN_MODE=security`) resolving correctly:

| Mode | Selected by | What runs |
|------|------------|-----------|
| `ops` (default for the `gaia` plugin) | registry `installed[].name == "gaia"` (or legacy `"gaia-ops"`) | All hooks, all modules, all agents, all skills, all tools, all config |
| `security` | legacy registry name `"gaia-security"`, or `GAIA_PLUGIN_MODE=security` | Hooks + modules only, no agents, no skills, no config |

### Detection Cascade (`hooks/modules/core/plugin_mode.py`)

```
1. plugin-registry.json    -- installed[].name: "gaia" or legacy "gaia-ops" -> ops; "gaia-security" -> security
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
| Skills | None | 32 skills injected per frontmatter |
| PreToolUse matchers | `Bash` only | `Bash`, `Task`, `Agent`, `SendMessage`, multi-tool |
| File write protection | `_is_protected()` blocks hooks/ and settings*.json for Edit/Write tools | Same -- fires regardless of permissionMode |

### Security Tiers (quick reference)

| Tier | Name | Side Effects | Approval |
|------|------|-------------|----------|
| T0 | Read-Only | None | No |
| T1 | Validation | None (local) | No |
| T2 | Simulation | None (dry-run) | No |
| T3 | Realization | Modifies state | Yes |

Enforcement: `blocked_commands.py` (permanent deny) + `mutative_verbs.py` (nonce-based approval). Everything not blocked and not mutative is safe by elimination.

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
npm run build:plugins          # builds the single `gaia` plugin to dist/gaia
npm run pre-publish:validate   # validates dist/ contents
npm run prepublishOnly         # build + validate (automatic before npm publish)
npm publish                    # publishes @jaguilar87/gaia
```

### Install (no npm postinstall -- bootstrap is lazy)

There is **no npm postinstall hook**. `package.json` carries an explicit `_install_note` documenting this: the DB is bootstrapped lazily on first `gaia` CLI use (`_ensure_db_bootstrapped` in `bin/gaia`, skipped only for the `install`/`uninstall` subcommands themselves), and workspace `.claude/` config is applied on demand via `gaia install` or by the SessionStart hook. `gaia install --postinstall` still exists as a flag for fail-soft, non-interactive invocation, but nothing in the npm lifecycle calls it automatically.

`gaia install` (interactive or `--postinstall`), first run (no `.claude/`):
1. Detect plugin mode (npm vs CC plugin) for diagnostic output.
2. Run `scripts/bootstrap_database.sh` -- seeds the schema, agent rows, and `schema_version`. Fail-loud in interactive mode (non-zero exit propagates); under `--postinstall` a failure writes `~/.gaia/last-install-error.json` and returns 0 so a wrapping flow does not abort.
3. Create `.claude/` if missing (created early so subsequent steps can write into it).
4. Merge permissions, env vars, and agent key into `settings.local.json` (preserves user config).
5. Merge hooks from `hooks.json` into `settings.local.json`.
6. Create `.claude/{agents, tools, hooks, config, skills}` symlinks (5) plus a `CHANGELOG.md` file link.
7. Write `plugin-registry.json` with `installed[].name == "gaia"` (the canonical identity; `gaia-ops` is recognized as a legacy name for registries written by older installs, never written fresh).
8. Write the `~/.local/bin/gaia` PATH launcher unless `--no-path`.

Note: no `project-context.json` is written. Project context lives in `~/.gaia/gaia.db`. Run `gaia scan` separately to populate it -- install never triggers a scan.

`gaia update` (`.claude/` exists): shares the same helpers via `_install_helpers.py` -- show version transition, create `settings.json` only if missing, merge permissions/env/hooks (union, preserves user config), recreate/fix broken symlinks, run schema migrations and re-seed agent permissions if `schema_version` is behind `EXPECTED_SCHEMA_VERSION`, verify hooks/Python/DB schema/config.

The hook invoker is `python3 <script>` rather than executing the script directly, so missing exec bits on cross-platform checkouts do not break the install.

### Symlinks Created

```
.claude/agents    -> node_modules/@jaguilar87/gaia/agents/
.claude/tools     -> node_modules/@jaguilar87/gaia/tools/
.claude/hooks     -> node_modules/@jaguilar87/gaia/hooks/
.claude/config    -> node_modules/@jaguilar87/gaia/config/
.claude/skills    -> node_modules/@jaguilar87/gaia/skills/
.claude/CHANGELOG.md (file link) -> node_modules/@jaguilar87/gaia/CHANGELOG.md
```

(`_SYMLINK_NAMES` + `_SYMLINK_FILES` in `bin/cli/_install_helpers.py` -- 5 directory symlinks plus one file link, not 7.)

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
| Hooks | `tests/hooks/modules/` | Security modules (mutative_verbs, blocked_commands, tiers, approval_grants, approval_scopes, command_semantics), tools (bash_validator, shell_parser, cloud_pipe_validator, task_validator), core (paths, state), context (context_writer) |
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

After `npm install -g @jaguilar87/gaia` (or via the local symlink) the dispatcher `gaia` is on PATH. Subcommands are discovered under `bin/cli/`.

| Command | Purpose | When to use |
|---------|---------|-------------|
| `gaia doctor` | Health check: schema, FTS5, symlinks, settings, hook files | After install, after update, debugging |
| `gaia status` | Installation snapshot: version, mode, DB path, last scan | Quick status check |
| `gaia metrics` | Usage analytics: tier distribution, agent invocations, anomalies | Performance analysis |
| `gaia history` | Session history viewer | Debugging past sessions |
| `gaia memory` | Episodic memory inspect/search | Recall past episodes, memory health |
| `gaia approvals` | List/accept/reject pending T3 approvals | Approval workflow |
| `gaia brief` / `gaia plan` | Brief and plan management against the DB substrate | Planning, brief lifecycle |
| `gaia context` | Display and refresh project context | Audit context state |
| `gaia paths` | Print resolved storage paths | Path debugging |
| `gaia workspace` | Workspace identity and consolidate operations | Multi-workspace setups |
| `gaia scan` | In-process project scanner | Refresh project context in ~/.gaia/gaia.db |
| `gaia install` | Bootstrap DB + workspace (run manually; no npm postinstall) | Fresh setup, manual repair |
| `gaia update` | Re-sync after a package upgrade | After bumping the version |
| `gaia cleanup` | Remove temp caches, old logs, `__pycache__` | Housekeeping |
| `gaia uninstall` | Disconnect Gaia from the workspace | Before package removal |

---

## 6. Metrics and Anomaly Detection

| Module | What It Tracks |
|--------|----------------|
| `audit/metrics.py` | Hook invocations, tier distribution, approval rates |
| `audit/event_detector.py` | Anomalous patterns in agent behavior |
| `audit/workflow_auditor.py` | Workflow compliance and audit trail |
| `gaia metrics` | CLI access to collected metrics |

---

## 7. Dev Workflow

### Dev Mode (symlinks to source)

```bash
# In any project directory:
ln -sf /home/jorge/ws/me/gaia-dev/agents   .claude/agents
ln -sf /home/jorge/ws/me/gaia-dev/hooks    .claude/hooks
ln -sf /home/jorge/ws/me/gaia-dev/skills   .claude/skills
ln -sf /home/jorge/ws/me/gaia-dev/tools    .claude/tools
ln -sf /home/jorge/ws/me/gaia-dev/config   .claude/config
```

Changes to source files take effect immediately (no build step).

### Release Mode (npm install)

```bash
npm install @jaguilar87/gaia
# postinstall creates symlinks: .claude/* -> node_modules/@jaguilar87/gaia/*
```

### Test Isolation

```bash
cd /tmp
mkdir test-project && cd test-project
npm init -y
npm install ~/ws/me/gaia-dev        # installs from local source
gaia doctor                          # verify installation
npm test                             # run L1 suite from gaia-dev
```

### Version Bump + Publish

```bash
npm version patch|minor|major        # bump in package.json
npm run build:plugins                 # rebuild dist/
npm run pre-publish:validate          # validate
npm publish                           # publish to npm
```

---

## 8. Validation Tools

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

### Approval Gate

```bash
python3 tools/validation/approval_gate.py
```

Validates T3 approval nonce lifecycle: generation, scope matching, expiry, grant/deny.

### Doctor

```bash
gaia doctor
```

Full system health: schema, FTS5 sync, hook reachability, symlink integrity, agent_permissions seed, Python environment, config file presence, settings.json/settings.local.json correctness.

---

## 9. Index, Not Snapshot

Principle: **project-context is index, not snapshot** of cloud state. It captures names, identifiers, relationships, and semi-stable metadata declared in code or config — not real-time runtime values.

### What belongs in the index (keep)

- Project and account IDs (stable identifiers)
- Cluster names declared in Terraform/Helm
- Region and environment labels
- Agent permission matrices
- Stack, language, and tooling metadata

### What does not belong in the index (retire)

| Category | Examples |
|----------|---------|
| Cloud resource runtime status | pod counts, instance status, VPC IDs, subnet lists |
| API-discovered facts | load balancer DNS names, IP addresses, OIDC IAM bindings |
| Scanner-produced live data | pubsub topic lists, secret manager enabled flag, monitoring status |

Any field whose scanner requires a live cloud API call (`gcloud`, `aws`, `kubectl`) to produce is live-state — it belongs in scanner output, not the index.

### How to obtain live state

Run the appropriate cloud CLI at the moment the question arises:

```bash
gcloud compute addresses list                        # static IPs
aws elbv2 describe-load-balancers                    # load balancers
kubectl get pods -n <namespace>                      # pod status
terraform show -json | jq '.values.root_module'      # TF state
```

Do not cache the result in project-context. A stale live-state field silently misrepresents today's infrastructure.

### Enforcement

- `config/cloud/gcp.json` and `config/cloud/aws.json`: `section_schemas` for live-state fields removed in B6. Scanner-populated fields go through the store API from B2+.
- `gaia/store/schema.sql`: DDL defines no columns for live-state narratives (`ci_cd_findings`, `cluster_discrepancy`). The schema cannot store what it does not define.
- `tests/unit/test_surface_routing_live_state.py`: guards that no `signals.keywords` in `surface-routing.json` references a retired live-state field name.
