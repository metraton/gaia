# Changelog: CLAUDE.md

All notable changes to the gaia-ops orchestration system are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Approval grants redesign: a grant is single-use and consumed at match (before the command executes), TTL cut from 60 to 5 minutes. Approving is now coupled to execution — approval triggers an automatic verbatim re-dispatch of the approved command instead of a separate resume step. `COMMAND_SET` batches simplified to the hook-minted path only (>= 2 T3 sub-commands blocked in one compound Bash call, content-derived `approval_id`, 5-minute TTL); the plan-first batch declaration flow and the `gaia approvals derive-id` CLI are retired — there is no agent-declared or CLI-derived batch id, only the one the hook mints from the blocked chain.
- `GAIA_SOURCE_ROOT` removed: no Gaia product command is env-var-dependent anymore. `resolve_source_root` (`gaia release check`/`publish`) now resolves the SOURCE checkout only via the executing copy or its git worktree root -- tier 2/3 of the old three-tier lookup; the env-override tier is gone, with no escape hatch. `gaia dev` now fails loud at its entrypoint when the copy it is physically loaded from is not a real source checkout (no `build/gaia.manifest.json` + `tests/`), instead of suggesting `export GAIA_SOURCE_ROOT=...`; it points the caller at `python3 <checkout>/bin/gaia dev` instead. `gaia doctor`'s install-provenance check no longer compares installed-vs-source mtimes (which needed `GAIA_SOURCE_ROOT` to locate source outside a git repo) -- it now only verifies a local (`file:`) install's `node_modules/@jaguilar87/gaia` resolves, self-sufficiently from the workspace's own `package.json`. Losing the "install is STALE vs source" nudge is an accepted trade-off for a fully self-sufficient product surface.

### Removed

- Cross-session surfacing of pending approvals: the SessionStart `[ACTIONABLE]` pending-approvals block and the per-turn pending feed are removed. Pending approvals (24h TTL, unchanged) no longer surface outside the turn that produced them.
- `consume_session_grants` mechanism, superseded by the consumed-at-match single-use grant model.
- `GAIA_SOURCE_ROOT` environment variable, and `doctor`'s freshness check (`_newest_source_mtime`) that depended on it.
- `metadata:` block (`user-invocable`, `type`) from every skill's SKILL.md frontmatter (all 34 skills under `skills/`). The block was inert -- no consumer in build, `doctor`, tests, or `skill-creation` ever read it -- so removing it drops dead schema rather than changing behavior. Frontmatter now carries only `name` + `description`. `skills/README.md` is reconciled: the SKILL.md format example no longer shows the block, and prose that described a skill as directly invocable via the Skill tool now says so in plain language instead of citing the retired `user-invocable` field.

### Fixed

- Schema v33 (workspace FK cascade): four audit-trail tables (`memory_history`, `agent_contract_handoffs`, `project_context_contracts_history`, `project_history`) referenced `workspaces(name)` without `ON DELETE CASCADE`, so `gaia context prune-workspaces` could fail on an FK violation and roll back the whole prune when a phantom workspace still carried residual audit rows. Added `ON DELETE CASCADE` to those four FKs, bumped `EXPECTED_SCHEMA_VERSION` to 33 (refreshed `schema.checksum`), and added the forward migration `scripts/migrations/v32_to_v33.sql` (table rebuild with `PRAGMA legacy_alter_table=ON`, since SQLite cannot alter a FK in place).
- `gaia context prune-workspaces --yes` is now correctly classified T3 (state-mutating): it hard-deletes `workspaces` rows, but the `context` group carried no mutative verb and classified read-only by elimination. Only the destructive subcommand is anchored (`COMMAND_SUBCOMMAND_MUTATIVE_UPGRADES[("gaia","context")]`); other `context` subcommands stay read-only. Separately, the Step 5 ALWAYS-dangerous flag scan now runs before the read-only-verb early return, so `git fetch --prune` (a read-only verb with a destructive flag) escalates to T3 instead of being skipped.
- SubagentStop M4 fence footgun: a turn that built its contract via the `gaia contract` CLI and ran `gaia contract finalize` (valid terminal row) but forgot to echo the fenced `agent_contract_handoff` in its response text was hard-rejected by the full-verdict gate. `adapt_subagent_stop` now reconstructs the envelope from the agent's own finalized draft when the fence is missing, so the gate parses the completed contract; non-fatal (falls back to the unchanged gate when no finalized row exists). The minted-agent-id resolver was factored into a shared `resolve_minted_agent_id` reused by the backstop, truncation salvage, and this path.

## [5.1.3] - 2026-07-07

### Changed

- `gaia dev` now prints an explicit restart notice on success (both modes), since the Claude Code harness pins hook commands at session start and does not hot-reload -- an open session keeps running the OLD hooks until restarted. It also prints a stateless `export GAIA_SOURCE_ROOT=<source>` suggestion (no sidecar, nothing persisted) for when the user wants `gaia doctor`/`release check` freshness from a workspace whose source lives outside a git repo.
- Docs: `gaia-release` skill consolidated -- documents that `gaia dev` is T3 and blocks for approval, runs no tests by design (the fast loop stays cheap), and prints the restart notice on success; documents that `release check`/`release publish` resolve the canonical source via `resolve_source_root` and fail loud when no source checkout is reachable; adds a troubleshooting entry for a T3 grant re-blocking on retry when the signing cwd differs from the retry cwd (use absolute paths).

## [5.1.2] - 2026-07-07

### Added

- `gaia doctor`: new install-provenance check that distinguishes a local working-tree install from an npm install, plus structural checks — component-naming (component name vs. its directory) and skill-cross-refs (dangling cross-references between skills).

### Changed

- `gaia dev` is now classified as T3 (state-mutating) and the `bin/gaia` dispatcher re-dispatches through the security classifier, giving launcher parity between the `gaia` launcher and `python3 <path>/bin/gaia` — the same command is classified identically no matter which entry point invokes it.
- `resolve_source_root`: `gaia release check` and `gaia release publish` now validate the canonical source tree rather than the installed copy under `.claude/`, so release validation no longer inspects a stale installed artifact.
- Docs: `security-tiers` and `gaia-release` updated for the new install flows.

### Fixed

- Dev-pack tarballs are now content-addressed, and stale-but-present symlinks are freshened/repointed on install. Previously a stale symlink left the installed copy diverged from source, so a local install was not reflected in the runtime; the install now repoints those symlinks so the running system matches the packed source.

### Removed

- `gaia release sync-local` subcommand, along with the workspace marker it relied on.

## [5.1.1] - 2026-07-06

## [5.1.0] - 2026-07-03

## [5.1.0-rc.4] - 2026-07-03

## [5.1.0-rc.3] - 2026-07-03

## [5.1.0-rc.2] - 2026-07-02

## [5.1.0-rc.1] - 2026-07-02

## [5.0.11] - 2026-06-30

### Changed

- Host decoupling (#88): la lógica del core (clasificación T0–T3, grants, validación, audit) queda desacoplada de Claude Code tras la capa adapter. Lo específico del host vive en seams: `host_session`, `host_transcript`, `registry`/`get_adapter`, `request_consent`/`ConsentRequest`, `HostCapability`/degradación, `HostDistribution`. Soportar un host nuevo de la familia hook-interception = escribir un adapter + declarar capacidades, sin tocar el core.

### Added

- Estado terminal `descoped` para acceptance criteria (descope deliberado, hard-terminal) más invariantes de `verify_brief` (`closed_brief_nonterminal_ac`, `closed_brief_open_plan`) para coherencia brief/plan/AC al cerrar.

### Fixed

- Endurecimiento del security-core a 100% killable (mutation testing) en `blocked_commands`, `mutative_verbs`, `tiers` y `approval_grants`. Arreglado el mecanismo de skip-file de equivalentes para casar por identidad estable (`operator|posición|occurrence`) en vez de `job_ids` regenerados — elimina la exclusión-cero silenciosa ("falso 100%") tras cada `cosmic-ray init`.
- Corregido el help de `brief close` (verify advisory, sin cascade de estado).

## [5.0.10] - 2026-06-29

## [5.0.9] - 2026-06-25

### Changed

- Harness events now persist exclusively to the `harness_events` table in `~/.gaia/gaia.db`. `event_writer.py` writes through `gaia/store/writer.py::write_harness_event`; the legacy `events.jsonl` append path is retired. The SessionStart "Recent Events" block in `context_injector.py` reads from `harness_events` via `cross_surface_query` and is remapped to the reader's row shape (`surface, timestamp, type, agent, summary, raw`).

### Removed

- Legacy `gaia plans` CLI subcommand (`bin/cli/plans.py`) — superseded by the plan tables and `gaia plan`.
- One-shot migration tooling `tools/migration/migrate_04_harness_events.py` and `.sh` — the harness-events cutover is complete and the migration is no longer needed.

## [5.0.8] - 2026-06-24

## [5.0.7] - 2026-06-12

### Fixed

- `gaia doctor` now passes (rc=0) on a clean install: the `commands` symlink (a removed surface) was dropped from the symlink checks, and `memory_fts5_count` now reads the canonical gaia.db store instead of the legacy search.db — eliminating the two warnings that made a fresh install report "degraded" in 5.0.5/5.0.6.

### Changed

- Release pipeline: the sandbox validation harness is now a PRE-publish gate (runs against the packed tarball before `npm publish`), and the harness fails if `gaia doctor` returns rc>=1 — so a degraded build can no longer be published.

## [5.0.6] - 2026-06-12

### Fixed

- **`gaia doctor` no longer reports a freshly-installed workspace as "degraded"
  (rc=1)** — an empty project-context contracts table is now `info` (an advisory
  to run `gaia scan`) instead of `warning`, so a clean install passes doctor with
  rc=0. Fixes the post-publish sandbox-validation failure seen in 5.0.5.

## [5.0.5] - 2026-06-11

### Repository Hygiene, Python 3.11 Floor, v18 Schema Floor + Drift Guard, Release-Pipeline Hardening

Maintenance release focused on shrinking the surface area and hardening the release pipeline. Dead and redundant surfaces are removed, Python 3.9 support is dropped (minimum is now 3.11), the database migration history is collapsed to a v18 floor backed by a new build-time schema-drift guard, git-format rules are inlined into their single consumer, and several release-pipeline bugs that caused CI re-triggers and stale local installs are fixed.

#### Added

- **Schema-drift guard wired into pre-publish validation** — a new build/pre-publish
  check (`scripts/check_schema_drift.py` + a recorded fingerprint in
  `scripts/migrations/schema.checksum`, wired into pre-publish-validate **Step 5c**)
  fails the build if `gaia/store/schema.sql` changes without a matching schema version
  bump and migration. This makes silent schema drift impossible to ship: any edit to
  the schema must be accompanied by a version bump, or the gate stops the release.

#### Changed

- **Python minimum is now 3.11 (Python 3.9 dropped)** — `pyproject.toml`
  `requires-python`, the ruff target version, and the CI test matrix all move to 3.11
  as the floor. The dead `scripts/check-py39-compat.py` compatibility checker and its
  npm alias are removed along with the 3.9 support.

- **Database migration history collapsed to a v18 floor** — fresh installs now stamp
  schema **v18** directly instead of replaying the full migration chain. Databases below
  the v18 floor are rejected cleanly with guidance to recreate, rather than attempting an
  unsupported incremental upgrade. The per-step `vN_to_vN+1` migration SQL and their
  obsolete version-specific tests are removed.

- **Git commit-format rules inlined into their single consumer** — the commit-format
  rules previously in `config/git_standards.json` are inlined directly into
  `commit_validator.py` (its only reader), and the standalone config file is removed.
  Forbidden-footer enforcement is consolidated into `bash_validator` (the duplicate dead
  check is removed); runtime AI-attribution footer stripping continues to live in
  `bash_validator`. The git-conventions skill is updated to match.

- **Release pipeline hardened** — `publish.yml` now runs on Python 3.11 and appends
  `[skip ci]` to the dist commit-back, stopping the CI re-trigger / version-churn loop
  that re-ran the pipeline on every published dist commit. `gaia:install-local` now
  rebuilds plugins before packing so a local install can never carry a stale `dist/`.
  `ci.yml` drops the obsolete `settings.json` build assertions, and the gaia-release
  skill drift is corrected.

- **pytest tmp uses the default OS tmpdir** — the in-repo pytest `basetemp` that
  polluted the working tree (and broke scanner project-identity isolation) is removed;
  tests now use the default OS temporary directory. `config/README.md` is rewritten to
  match the actual directory contents.

#### Removed

- **Dead and redundant surfaces and artifacts** — removed `evidence/`, `docs/`,
  `git-hooks/` (the redundant `commit-msg` sed copy; runtime footer stripping stays in
  `bash_validator`), `tools/agentic-loop/`, `tools/review/`, `logs/`, the `commands/`
  slash-command surface (including `/gaia`), the `templates/` managed-settings surface,
  and `config/crons-schema.md`. These were either unused, duplicated by an active code
  path, or superseded surfaces with no remaining consumer.

## [5.0.4] - 2026-06-06

### COMMAND_SET Batch Approval, Consent-Reducing Approval Verbs, Contract Advisory Field, Version Source Sync

Patch release superseding 5.0.3 (which was never published to npm due to a pyproject.toml version drift that failed pre-publish validation). This release adds the version source sync fix on top of all 5.0.3 changes: COMMAND_SET batch-approval wired end-to-end, consent-reducing approval verbs reclassified out of T3, advisory contract field added, redundant `gitops_validator` removed, and all version sources (package.json, pyproject.toml, .claude-plugin/plugin.json, .claude-plugin/marketplace.json, CHANGELOG.md) aligned. Full suite green (4555 passed).

#### Added

- **COMMAND_SET batch approval, end-to-end** — a payload carrying a `command_set`
  of more than one mutative command now activates into ONE `COMMAND_SET` grant
  covering the whole batch instead of being degraded to a single command. The
  create side (`activate_db_pending_by_prefix` Step 3b in `approval_grants.py`,
  fed by `_intake_command_set_pending` in `handoff_persister.py` and persisted via
  `gaia/store/writer.py`) was previously orphaned; it is now wired to the
  byte-for-byte consume path in `bash_validator`. The batch is consumed
  item-by-item under a single consent.

- **Advisory `user_facing_summary` field on the agent contract** — an additive,
  optional field in the `agent_contract_handoff` envelope (`contract_validator.py`,
  `response_contract.py`) carrying a human-readable summary for the orchestrator to
  surface. Purely additive; absence does not affect validation.

#### Changed

- **Consent-reducing approval verbs are no longer T3** — `gaia approvals
  revoke|reject|reject-all|clean` only revoke or discard grants Gaia itself issued
  (they reduce capability, never reach remote state), so they are reclassified out
  of T3 via `CONSENT_REDUCING_SUBCOMMAND_EXCEPTIONS` in `mutative_verbs.py`. `gaia
  approvals approve` *grants* capability and remains T3.

- **`gaia approvals revoke` unified with auto-detect** — `revoke` now auto-detects
  a pending approval (pending → grant) and the separate `revoke-v2` command was
  removed. Behavior is otherwise unchanged.

- **Plan-first heuristic** — COMMAND_SET is now treated as a judgment call, not a
  default, when deciding how to present batched mutative work.

#### Fixed

- **Guard empty/None `transcript_path`** — `transcript_reader.py` now guards against
  an empty or `None` transcript path instead of failing downstream during nonce
  extraction.

- **Harden AI-attribution footer stripping** — the attribution-footer stripping in
  `bash_validator.py` is hardened against additional footer shapes.

#### Removed

- **Redundant `gitops_validator`** — `hooks/modules/security/gitops_validator.py` and
  its test are removed; its responsibilities are covered by the unified bash
  validation path. All references (security `__init__`, `bash_validator` import/call,
  simulator extractor, surface-routing config, architecture docs, and skill/README
  references) are cleaned up.

## [5.0.2] - 2026-06-03

### Approval-Flow Hardening, mkdir Reclassification, Jira Skill

Patch release accumulating security and approval-flow fixes, one new skill, and a quality-of-life exemption for Gaia's own planning bookkeeping commands. All 4575 tests pass on a clean install.

#### Fixed

- **Stop double-approval on re-dispatched T3 grants** — a T3 command that was
  re-dispatched after approval could be blocked a second time with a fresh nonce,
  forcing the user to approve the same operation twice. Two gaps caused the grant miss:
  `command_semantics` was not normalizing output-redirect tokens out of the semantic
  signature (causing the retry signature to drift from the approved one), and
  `bash_validator._find_pending_in_db` was matching too narrowly and minting a new
  nonce instead of reusing the granted one. Both gaps are closed; a regression test
  reproduces the redirect-normalization grant miss.

- **Flag-classifier grants + cross-session grant matching** — the flag-classifier
  branch in `bash_validator` was never consulting approval grants, so curl-family T3
  commands that had been approved were blocked again on retry. `check_db_semantic_grant`
  is now session-agnostic (session is audit-only); `_find_pending_in_db` accepts
  `all_sessions=True`; grant insert is fingerprint-idempotent so cross-session
  block→approve→retry converges. `matches_approval_signature` derives identity from
  `analyze_command` only; `_normalize_flag_token` binds long `--flag=value` tokens to
  fix a critical over-match. Grant TTL raised from 5 to 60 minutes
  (`APPROVAL_GRANT_TTL_MINUTES`), kept distinct from the 1440-minute pending TTL.

- **Unify T3 decision across bash validator classifiers** — mutative-verb,
  `file_to_exec` composition, and flag-mutation classifiers now all route through a
  single `decide_t3_outcome()` keyed on `has_orchestrator_above` (is_subagent AND
  is_ops_mode). `file_to_exec` and curl flag-mutations no longer hardcode the native
  CC approval dialog; in ops+subagent mode they produce `deny+approval_id` like
  mutative verbs, keeping them inside the Gaia approval/audit trail. Local workspace
  data files (`.json`/`.yaml`/`.csv`/`.txt`) are degraded to ALLOW for the
  `file_to_exec` composition; network/decode→exec pipelines still BLOCK.

- **`mkdir` reclassified as T0 for non-sensitive working-tree paths** — `mkdir` on
  relative, home-relative, or absolute non-system paths is non-destructive and
  idempotent with `-p`; it no longer triggers T3. `mkdir` targeting kernel
  pseudo-filesystems or privileged OS directories (`/dev`, `/sys`, `/proc`, `/etc`,
  `/boot`, `/usr`, `/bin`, `/sbin`, `/lib`, `/lib64`, `/root`) retains T3. Scratch
  space (`/tmp`, `/run`) is excluded — ephemeral, world-writable by design. Adds
  `MKDIR_SENSITIVE_PATH_PREFIXES` (11 prefixes) and `_mkdir_targets_sensitive_path()`
  in `mutative_verbs.py`.

#### Added

- **Schema v18 — stable project identity** — `project_identity` column and a partial
  unique index on the `projects` table collapse the same physical repo scanned from
  different vantages into one row. `store_populator.resolve_project_identity()` derives
  stable identity from git-common-dir → normalized remote → realpath. Migration files:
  `scripts/migrations/v17_to_v18.sql` and `v17_to_v18_fresh.sql`.

- **Skill `jira-ticket-writing`** — technique skill for writing human-readable Jira
  Stories and Subtasks following Atlassian conventions: structured title formula,
  acceptance criteria, story points, label taxonomy, and worked examples. User-invocable
  (`user-invocable: true`); not injected into any agent frontmatter by default.

#### Changed

- **`gaia brief` / `gaia ac` exempted from T3 gate** — `gaia brief <verb>` and
  `gaia ac <verb>` (`edit`/`set-status`/`set-field`/`add`/`remove`/`new`/`show`/`list`)
  now classify as non-mutative. Local planning bookkeeping that is reversible and has no
  external side effects is treated like `git commit`. The exemption is anchored to
  `(base_cmd, subcommand)` — not a generic `gaia *` pattern — so the consent layer
  (`gaia approvals approve/revoke`) and other groups (`gaia memory`) remain T3.
  Whole-record destruction (`gaia brief delete`) and dangerous flags (`--force`) still
  re-gate.

## [5.0.0] - 2026-06-02

### Stable Release: Scan Overhaul, Zero-Dep Install, Soft-Delete, DB-Canonical Context

Fifth major release of Gaia. Promotes the rc.7 release candidate to stable after passing the full dry-run, CI, and live-install gate. The headline work is a ground-up rewrite of the workspace scanner, a zero-dependency NPM install path, a soft-delete model for projects and workspaces, and the retirement of `project-context.json` in favour of the DB as the single canonical source of project context.

#### Added

- **Scan overhaul — taxonomy and recursive discovery** — `gaia scan` now classifies
  discovered items across three orthogonal dimensions: *workspace* (the Claude Code
  working environment), *project* (the user's source tree), and *installation* (the
  Gaia artefacts wired into `.claude/`). Discovery walks recursively so nested
  monorepo structures and workspace-within-workspace layouts are captured correctly.
  Taxonomy is defined in `tools/scan/` and tested independently of the CLI.

- **On-demand `gaia scan <path>`** — the scan subcommand now accepts an explicit
  target path, enabling agents to scan a directory that is not the current working
  directory without changing cwd. Useful for multi-root workspaces and cross-project
  context enrichment.

- **Scan/install separation + scan-core** — the scan pipeline is now split into a
  pure discovery core (`scan-core`) with no install-time side effects, and a
  separate install phase that consumes core output. This makes scan deterministic
  and testable without triggering postinstall hooks, and lets the install phase be
  skipped when scanning for context only.

- **Pure-NPM zero-dependency install** — `postinstall` now completes with zero
  runtime npm dependencies. All install-time logic runs through `python3 bin/gaia
  install --postinstall` (Python stdlib only). The devDependencies remain for build
  tooling (`chalk`, `eslint`) but consumers take no transitive runtime deps.

- **Soft-delete for projects and workspaces** — `gaia scan` handles pruning
  automatically: when a previously-registered project path is no longer found on
  disk, the prune pass marks it missing; scanning a directory that has no Gaia
  installation demotes the workspace (marks it missing) and tombstones its
  projects. No explicit remove or demote commands exist — lifecycle state flows
  from the scanner. Soft-deleted rows are hidden from list views. Schema migrated
  from v12 to v17 to carry the new columns and the `project_workspace_archive`
  table.

- **`project-context.json` retired — DB is canonical** — the on-disk
  `project-context.json` file is no longer written or read by any Gaia component.
  Project context lives exclusively in `~/.gaia/gaia.db` (tables `projects`,
  `workspaces`, `project_resources`). The context provider and all CLI subcommands
  read directly from the DB. Existing `project-context.json` files are ignored on
  upgrade; run `gaia scan` to populate the DB.

#### Fixed

- **`gaia approvals list` crash** — `bin/cli/approvals.py` raised an unhandled
  exception when the `approval_grants` table contained rows with a `None` nonce
  (rows inserted by older schema versions). Added a null-guard before nonce
  formatting; the command now lists all rows cleanly and marks legacy rows as
  `(no nonce)`.

#### Changed

- **Schema v12 → v17** — five incremental migrations applied in lockstep with
  `EXPECTED_SCHEMA_VERSION` in `bin/cli/doctor.py` and the bootstrap insert in
  `scripts/bootstrap_database.sh`. The `test_schema_version_lockstep.py` test
  confirms all three agree.

- **CI hardening** — `ci.yml` now runs the full pytest suite on Python 3.9, 3.11,
  and 3.12 in parallel, blocks merges on any failure, and verifies `build:plugins`
  produces valid `dist/` artefacts. The `validate-sandbox.sh` harness is wired
  into the publish gate.

- **Suite green** — all Layer 1 tests pass on the three supported Python versions.
  The scan-core and soft-delete paths are covered by dedicated test modules.

- **`bin/validate-sandbox.sh`** -- harness now drives `gaia` subcommands end
  to end (no `gaia-X.js` callers remain). The 8-check matrix is unchanged.
  Sandbox DB is now isolated via `GAIA_DATA_DIR` so memory checks run against
  a seeded fixture DB rather than the global `~/.gaia/gaia.db`.

- **CLI docstrings** -- `bin/cli/*.py` modules dropped the
  "Mirrors gaia-X.js" parity comments now that there is no JS counterpart on
  disk to mirror.

#### Removed

- **Legacy JS CLI binaries** -- `bin/gaia-doctor.js`, `bin/gaia-status.js`,
  `bin/gaia-history.js`, `bin/gaia-metrics.js`, `bin/gaia-cleanup.js`,
  `bin/gaia-update.js`, `bin/gaia-uninstall.js`, `bin/gaia-skills-diagnose.js`,
  `bin/gaia-review.js`, `bin/gaia-evidence`, `bin/gaia-scan` (Node wrapper),
  and `bin/gaia-scan.py` are gone. The `bin` field in `package.json` now
  exposes a single binary: `gaia`. Every subcommand previously available as
  `npx gaia-X` is now reached through `gaia X` -- subcommands are discovered
  automatically from `bin/cli/*.py` via the `register()` / `cmd_<name>()`
  contract. Lifecycle scripts (`postinstall`, `preuninstall`) call
  `python3 bin/gaia install --postinstall` and `python3 bin/gaia uninstall
  --preuninstall` directly. `gaia-skills-diagnose`, `gaia-review`, and
  `gaia-evidence` had no Python successor and are not migrated; for general
  health checks use `gaia doctor`.

#### Internal

- Regenerated `dist/gaia-ops/` and `dist/gaia-security/` for 5.0.0.
- `pyproject.toml` version aligned with `package.json` at `5.0.0`.

---

## [5.0.0-rc.3] - 2026-04-26

### Release Candidate 3: Python 3.9 Compatibility Fix

Hotfix for rc.2. The previous release shipped successfully to npm under
the `@rc` dist-tag but failed its post-publish sandbox harness gate
because `bin/cli/approvals.py` used PEP 604 union syntax (`X | None`)
which requires Python 3.10+ at module-import time. The publish.yml
runner pins Python 3.9, and the `ci.yml` test matrix also includes 3.9.
The plugin loader caught the resulting `ImportError` and emitted a
`Warning:` line that leaked into stdout, breaking JSON parsing for
several `gaia` subcommands on 3.9-only environments.

#### Fixed
- **Python 3.9 compatibility** — added `from __future__ import annotations`
  to 7 files that used PEP 604 union syntax without it. With deferred
  annotation evaluation, the type hints become string literals and no
  longer execute the `|` operator at definition time. A repo-wide audit
  of 21 PEP-604 files confirmed 14 were already safe (had `__future__`)
  and 7 were the actual 3.9 breakers; all 7 are now fixed:
  - `bin/cli/approvals.py` (the publish.yml-failing one)
  - `bin/cli/plans.py`
  - `bin/cli/context.py`
  - `tests/cli/test_gaia_context.py`
  - `tests/cli/test_gaia_plans.py`
  - `tools/scan/tests/conftest.py`
  - `tools/agentic-loop/record-iteration.py`

The audit also confirmed no PEP 634 `match` statements, no `TypeAlias`,
no runtime PEP 604 in `isinstance()`, and no runtime parameterized
stdlib generics, so the `__future__` route is sufficient — no actual
type-hint rewrites required.

5.0.0-rc.2 is superseded by this release. Users on Python 3.10+ were
unaffected by the bug; users on Python 3.9 should upgrade to rc.3.
Failing run for reference:
https://github.com/metraton/gaia/actions/runs/24951053090

## [5.0.0-rc.2] - 2026-04-26

### Release Candidate 2: Converger Identity, Session Liveness, Install-Gate Hardening

Second release candidate for v5.0.0. Adds the orchestrator's Converger
("Cerrar") conversational closure identity, real-PID session liveness in the
registry, the `agent-creation` and `session-reflection` skills, and an
end-to-end consumer-install validation harness that now actually exercises the
gate. Three install-time bugs surfaced and were fixed alongside the harness
that found them.

#### Added
- **Converger identity for orchestrator** — "Cerrar" conversational closure
  framing. Brief-spec reframed as closure ritual (Size gate removed),
  `planning_specs` surface routing narrowed to explicit artifact keywords,
  architecture docs aligned with closure framing.
- **session-reflection skill** — conversational session-close ritual. Surfaced
  by orchestrator at session end; complements `gaia-compact`.
- **agent-creation skill** — coach skill for designing new agents end-to-end:
  identity, tool surface, contract, and verification.
- **SessionEnd hook + PID liveness** — `session_end_hook.py` for clean
  unregister; session_registry now uses real PID + `/proc` starttime to detect
  liveness across sessions. `Stop` hook no longer mutates the registry (was
  causing premature unregister mid-conversation).
- **validate-sandbox.sh** — end-to-end consumer-install verification harness.
  Two targets: `--target sandbox` (ephemeral fixture project) and
  `--target local` (real workspace install with `--workspace` override). Eight
  pass/fail checks: version, doctor, status, context show, memory stats,
  memory search, scan, settings preservation. Wired into `publish.yml` so
  every release smoke-tests the published tarball before notifying success.
- **`gaia:verify-install:{local,rc,latest}` and `gaia:install-local`** scripts
  in package.json for manual local validation against tarballs or registry.

#### Changed
- **REVIEW → APPROVAL_REQUEST** rename across active doctrine (state machine,
  skills, hooks). Comments and references in `hooks/**` updated. The previous
  `REVIEW` state caused confusion with the human review activity; the new name
  reflects what the state actually represents (an agent requesting human
  approval for a specific T3 operation).
- **Stop hook decoupled from registry** — Stop event no longer mutates
  session_registry. SessionEnd handles unregister cleanly; this avoids the
  Stop-then-resume race where the registry would drop a still-active session.
- **`publish.yml`** — sandbox harness step added after npm publish; waits for
  registry propagation, then runs validate-sandbox.sh against the freshly
  published tarball as a smoke test.

#### Fixed
- **Sandbox harness on noexec /tmp** — validate-sandbox.sh now detects
  `noexec` mounts via `findmnt` (with `/proc/mounts` fallback) and falls back
  to `$TMPDIR` → `/tmp` → `$HOME/.cache/gaia-sandbox`. Previously the harness
  was unrunnable on WSL/Linux setups with `noexec /tmp` (rc=126 Permission
  denied on the installed bin shims); the gate appeared to validate but never
  actually ran.
- **`gaia scan` harness check** — was invoking bare `gaia-scan --dry-run`,
  which routes to `gaia-scan.py` whose argparse rejects `--dry-run`. Now uses
  `gaia context scan --dry-run` (the higher-level CLI subcommand that does
  accept `--dry-run`); drops the dead fallback.
- **doctor `<lambda>` check** — `cmd_doctor` wrapped each check in a bare
  `lambda`, so any exception surfaced as `'<lambda>'` in the JSON output
  hiding which check actually failed. Replaced with `functools.partial` so
  `__name__` resolves to the wrapped function (e.g. `check_project_dirs`).
- **doctor `check_project_dirs` PosixPath/list TypeError** — code did
  `project_root / dir_path` while iterating `paths.items()`; when a value was
  a list (e.g. `"scan_targets": ["."]`), `Path / list` raised TypeError.
  Values are now normalized to a flat sequence of `(label, str)` pairs before
  joining; list values expand into `label[0]`, `label[1]`, ...
- **postinstall FTS5 backfill on fresh install** — `maybeBackfillFts5()`
  returned early when `search.db` was missing with comment "doctor --fix will
  create it on first use", but nothing in the install flow runs `doctor --fix`
  automatically. A consumer reinstalling after `gaia uninstall` (which scrubs
  search.db) would have an empty FTS5 index until manual intervention. The
  early return is gone; missing search.db now falls through to `doctor --fix`
  which creates and populates the index.
- **postinstall dynamic package resolution** — `gaia-update.js` now resolves
  the gaia package name from `node_modules/@jaguilar87/` instead of
  hardcoding, supporting both the v5+ `gaia` name and legacy `gaia-ops`. Also
  detects and repairs symlinks pointing at the legacy path.
- **memory sentinel return** — sentinel value returned with a surfaced warning
  instead of a silent failure when memory paths fail to resolve.

#### Internal
- **Regenerated plugin artifacts** — `dist/gaia-ops/` and `dist/gaia-security/`
  rebuilt for rc2.
- **Cross-session liveness test** — real PID isolation in
  `session_registry` test fixtures.

## [5.0.0-rc1] - 2026-04-21

### Release Candidate: Context Evals, Planner M1-M6, Memory CLI, Security Hardening

First release candidate for v5.0.0. Consolidates the agentic-loop evaluation
framework, the closed gaia-planner milestones, the unified `gaia memory` CLI,
and a round of security hardening covering approval lifecycle, Gmail policy,
and session compaction.

#### Added
- **Context-evals framework** — full pytest-driven evaluation suite for agent
  context consumption. 5 graders (code, contract, trace, routing,
  skill-injection), 3 backends (static, headless, live), 10 scenarios in
  catalog, baseline snapshot with drift detection, and reporter for CI-friendly
  output. Tests under `tests/evals/` with `baseline.json` tracked and
  `{timestamp}-smoke.json` gitignored.
- **gaia-planner M1-M6 closed** — brief-spec + gaia-planner agent pipeline
  end-to-end. Includes plan state machine, REVIEW -> APPROVAL_REQUEST split,
  session_registry liveness filter, and approvals-drift-fix closed 2026-04-20.
- **gaia memory CLI** — `python3 bin/gaia memory` subcommand with search
  (`gaia memory search`), episode inspection (`gaia memory show <id>`), FTS5
  full-text index, scoring overhaul, and session context orientation.
- **gaia-compact skill** — structured session compaction preserving decisions,
  components, gaps, file map, and next steps. Invoked via `/compact` or
  orchestrator-level "compacta" triggers.
- **tools/__init__.py** — namespace marker for pytest rootdir parity. Resolves
  8 collection errors when running full suite (tests goes to 3702 passed,
  36 skipped, 0 errors).

#### Changed
- **Gmail policy** — macro-prefix fix: `+` in label prefixes now correctly
  strips before state-machine classification. Reply classified as mutative
  (was previously read-only, causing false negatives in T3 flow).
- **Approval workflow docs** — documented that `permissionMode` does not
  survive SendMessage resume. Subagents emitting APPROVAL_REQUEST mid-task
  require orchestrator to re-dispatch fresh (mode does not inherit on resume).
- **Package version** — `package.json` aligned with `pyproject.toml` at
  `5.0.0-rc1` (previously drifted at `5.0.0-beta.9`).

#### Fixed
- **pytest collection** — `tools/__init__.py` prevents rootdir walk-up mismatch
  between `tests/` and `tools/scan/tests/`. Full suite now collects cleanly.
- **Evals smoke JSONs** — transient artifacts no longer tracked in git;
  `tests/evals/results/*-smoke.json` gitignored, `baseline.json` preserved.

### Unified Python CLI + JS CLI Deprecation (inherited from beta cycle)

The JS CLIs (`gaia-status`, `gaia-doctor`, `gaia-cleanup`, `gaia-update`, `gaia-history`, `gaia-metrics`) are now deprecated in favor of the unified `bin/gaia` Python CLI. The JS CLIs remain functional but print deprecation warnings to stderr on every invocation.

#### Migration: Old Command → New Command

| Old JS command | New unified command |
|---|---|
| `npx gaia-status` | `python3 bin/gaia status` |
| `npx gaia-doctor` | `python3 bin/gaia doctor` |
| `npx gaia-cleanup` | `python3 bin/gaia cleanup` |
| `npx gaia-update` | `python3 bin/gaia update` |
| `npx gaia-history` | `python3 bin/gaia history` |
| `npx gaia-metrics` | `python3 bin/gaia metrics` |

#### New commands with no JS equivalent

The unified CLI also provides subcommands that did not exist as standalone JS CLIs:

| New command | Description |
|---|---|
| `python3 bin/gaia approvals list` | List pending T3 approval requests |
| `python3 bin/gaia approvals show APPROVAL_ID` | Show approval detail |
| `python3 bin/gaia approvals reject NONCE` | Reject a pending approval |
| `python3 bin/gaia approvals clean` | Remove expired grants |
| `python3 bin/gaia approvals stats` | Show approval statistics |
| `python3 bin/gaia plans list` | List all feature briefs |
| `python3 bin/gaia plans show BRIEF_NAME` | Show a brief and plan |
| `python3 bin/gaia context show` | Display project-context.json summary |
| `python3 bin/gaia context scan` | Refresh project-context via the scanner |

#### Deprecation timeline

- **Now (M6):** JS CLIs print `[DEPRECATED]` warnings to stderr. All functionality remains intact.
- **Future version (TBD):** JS CLIs will be removed from `package.json` bin field.

#### Why a unified CLI?

- Zero external dependencies (stdlib only, Python 3.9+)
- Single entry point: `bin/gaia --help` for all subcommands
- Machine-readable `--json` output on all subcommands
- Consistent exit codes: 0=ok, 1=warnings, 2=errors
- Extensible: add subcommands by dropping a `bin/cli/<name>.py` file

---

## [4.5.0] - 2026-03-24

### Settings Architecture Redesign + Multi-Cloud Security

Unified approach for permissions across NPM and plugin installation modes. Permissions now live in `settings.local.json` (union merge, preserves user config). `settings.json` contains only hooks.

#### Added
- **Azure deny rules** — 39 rules covering resource groups, networking, AKS, Key Vault, CosmosDB, Service Bus, and more
- **Generic wildcard deny rules** — 20 rules that catch all present and future cloud services (`aws * delete-*`, `az * delete`, `gcloud * delete`, etc.)
- **Indirect execution detection** — Catches `bash -c`, `eval`, `python3 -c`, `node -e`, `ruby -e`, `perl -e` wrappers that bypass regex patterns
- **Managed settings template** — `templates/managed-settings.template.json` for enterprise deployment via Claude.ai Admin Console
- **`updateLocalPermissions()`** in `gaia-update.js` — NPM postinstall now merges permissions into `settings.local.json` (same approach as plugin SessionStart)
- **Plugin mode detection via `plugin.json`** — `plugin_setup.py` and `plugin_mode.py` now read `.claude-plugin/plugin.json` for reliable name/version/mode detection with `--plugin-dir`
- **First-run welcome message** — `user_prompt_submit.py` detects first run and injects a welcome explaining that restart is needed to activate permissions

#### Changed
- **`settings.template.json`** — Removed permissions block; template now contains only hooks + environment
- **`_DENY_RULES` centralized in Python** — Single source of truth in `plugin_setup.py`, shared by both OPS and SECURITY modes
- **T3 approval flow** — All T3 mutative operations now use native `ask` dialog (both ops and security mode). Nonce workflow removed from direct conversation; kept for subagent use via skills.
- **`approval_messages.py`** — Simplified T3 block message to minimal data (tier + nonce). Workflow instructions live in skills, not hook messages.
- **`pre_tool_use.py`** — Simplified: passes through `block_response` from `bash_validator` directly, no more mode-specific branching
- **`bash_validator.py`** — T3 mutative returns `ask` response directly (no nonce generation, no pending files)
- **`session_start.py`** — Uses `mark_done=False` so `user_prompt_submit.py` can detect first-run and show welcome before marking initialized
- **`gaia-update.js` registry path** — Fixed to write `plugin-registry.json` in `.claude/` (same path Python hooks expect)
- **`gaia-doctor.js`** — Now checks permissions in `settings.local.json` (not just `settings.json`). Updated agent and config file lists.
- **`gaia-update.js` health check** — Updated config files (`surface-routing.json`) and agent list (`gaia-system.md`, `speckit-planner.md`)

#### Fixed
- **Registry path mismatch** — `gaia-update.js` wrote to `.claude/project-context/`, Python read from `.claude/`. Now both use `.claude/`.
- **Orphaned nonce files** — `bash_validator` no longer writes pending approval files for `ask` responses
- **Plugin mode detection** — `--plugin-dir` now correctly detects `gaia-ops` vs `gaia-security` via `plugin.json` instead of path parsing
- **First-run welcome race condition** — `SessionStart` no longer marks initialized; `UserPromptSubmit` marks after showing welcome
- **`_build_welcome()` framing** — Rewritten to explain WHY the user needs to restart (permissions not active yet), making Claude naturally relay the message

## [4.4.0-rc.5] - 2026-03-19

### Identity Redesign

Orchestrator identity is now minimal (~900 chars) and delegates to on-demand skills. CLAUDE.template.md deleted -- the UserPromptSubmit hook is the single source of truth for orchestrator identity.

#### Added
- **`skills/project-dispatch/SKILL.md`** (Reference type) -- agent routing table and dispatch rules, loaded on-demand via Skill tool
- **`skills/agent-response/SKILL.md`** (Protocol type) -- contract status handling, loaded on-demand via Skill tool
- Plugin distribution: `.claude-plugin/plugin.json` manifest with engines + categories for Claude Code native plugin system
- Self-hosted marketplace: `.claude-plugin/marketplace.json` with 2 sub-plugin tiers (gaia-security, gaia-ops)
- Adapter layer: `hooks/adapters/` with normalized types, abstract base, and Claude Code adapter
- `hooks/hooks.json` for plugin-channel hook configuration
- Distribution channel detection (`hooks/adapters/channel.py`)
- Integration tests for adapter -> business logic -> response flow
- Plugin manifest validation tests

#### Changed
- **`hooks/modules/identity/ops_identity.py`** -- reduced to ~900 chars; tells orchestrator to load skills on-demand instead of embedding all instructions inline
- **SendMessage validation** -- moved from invalid hook event to PreToolUse matcher (agent ID format + nonce approval check)
- **`hooks/modules/scanning/scan_trigger.py`** -- imports `tools.scan` directly (no `bin/` dependency), works in both npm and plugin mode
- **Agent namespace support** -- accepts both `cloud-troubleshooter` and `gaia-ops:cloud-troubleshooter` forms
- **`hooks/user_prompt_submit.py`** -- calls `ensure_plugin_registry()` as fallback if SessionStart didn't fire
- **`hooks/modules/context/context_injector.py`** -- path fixes for plugin mode
- **`hooks/modules/session/session_event_injector.py`** -- path fixes for plugin mode
- Hook entry points (pre_tool_use.py, post_tool_use.py, subagent_stop.py) now use adapter layer for stdin/stdout
- hook_response.py delegates to ClaudeCodeAdapter internally
- npm dist-tag now derived from version suffix (rc -> next, beta -> beta, etc.)

#### Removed
- **`templates/CLAUDE.template.md`** -- identity now injected dynamically; no generated CLAUDE.md
- **`copy_claude_md()`** in `tools/scan/setup.py` -- deprecated to no-op (callers still reference it for backward compat)

## [4.0.0] - 2026-03-03

### Breaking: Contracts as Single Source of Truth

Contracts now fully control what context each agent receives. Removed the progressive disclosure layer that was silently overriding contract definitions, and cleaned up ~400 lines of dead code from context_provider.py.

#### Changed
- **context_provider.py**: Contracts are the single source of truth -- removed progressive disclosure filtering that overrode contract-defined sections
- **context_provider.py**: Simplified output payload -- removed `enrichment` and `progressive_disclosure` keys from response
- **contracts/platform-architect.json**: Now reads `cluster_details` and `application_services` sections
- **contracts/gitops-operator.json**: Now reads `gcp_services` section (GCP overlay)
- **pre_tool_use.py**: Updated log message to show sections count and rules count
- **templates/CLAUDE.template.md**: Synced agent routing descriptions with CLAUDE.md

#### Fixed
- **context_provider.py `get_contracts_dir()`**: Path traversal went up 2 levels instead of 3, producing wrong directory -- masked by legacy fallback that silently compensated

#### Removed
- **context_provider.py**: ~400 lines of dead code:
  - Progressive disclosure engine (section filtering, phase-based visibility)
  - `LEGACY_AGENT_CONTRACTS` dictionary (hardcoded fallback contracts)
  - Semantic enrichment pipeline
  - `validate_project_paths()` function
  - Path resolution utility functions

#### Tests
- **tests/tools/test_context_provider.py**: Complete rewrite -- 8 tests covering all 6 agents, payload structure, and invalid agent handling

## [3.15.1] - 2026-02-24

### Fix: Cross-Layer Consistency & Dead Code Cleanup

Comprehensive audit of skills, hooks, and security modules. Fixed inconsistencies between layers that caused silent failures (tests pass but system broken).

#### Fixed
- **bash_validator**: Check blocked commands BEFORE safe commands (defense-in-depth order was inverted)
- **tiers.py**: Split `VALIDATION_PATTERNS` into `T1_PATTERNS` (validate, lint, fmt, check) and `T2_PATTERNS` (plan, template, diff) — aligns with security-tiers skill
- **tiers.py**: Removed `terraform plan` from `ULTRA_COMMON_T0_COMMANDS` fast-path (was T0, should be T2)
- **safe_commands.py**: Removed `terraform plan`/`terragrunt plan` from `ALWAYS_SAFE_MULTIWORD` (simulation, not read-only)
- **safe_commands.py**: Removed `python3`, `python` from `always_safe` (can execute arbitrary code)
- **safe_commands.py**: Removed `tar`, `gzip`, `gunzip`, `zip`, `unzip` from `always_safe` (modify filesystem)
- **task_validator.py**: Removed legacy `APPROVAL_INDICATORS` (`'validation["approved"] == True'`, `"Phase 5: Realization"`)
- **task_validator.py**: Added `speckit-planner` to `META_AGENTS`
- **pre_tool_use.py**: Resume regex `{6,7}` → `{5,}` to accept real Claude Code agent IDs
- **pre_tool_use.py**: Session events now inject BEFORE `# User Task` marker (was after)
- **post_tool_use.py**: Added `fcntl.flock` to prevent race conditions on `context.json`
- **post_tool_use.py**: Guard empty timestamps in retention filter
- **subagent_stop.py**: Fixed indentation bug in consecutive failure detection
- **subagent_stop.py**: Use `deque(f, maxlen=7)` instead of `f.readlines()` for metrics.jsonl
- **settings.json**: Moved 7 T3 commands from `allow` → `ask`: kubectl exec/label/annotate/uncordon, helm rollback, flux suspend/resume
- **settings.json**: Added `flux create` to `ask` list (was unprotected)
- **agent-protocol skill**: Removed `CURRENT_PHASE` from AGENT_STATUS (redundant with `PLAN_STATUS`)
- **agent-protocol skill**: `PLANNING` state now explicitly emitted in Phase 2
- **execution skill**: Scope clarified as T3-only (was accidentally broadened to T2)
- All 3 hooks: Removed `logging.StreamHandler()` (was sending noise to stderr)

#### Removed
- **`config_loader.py`** — Dead code, never imported by any module
- **`discovery_classifier.py`** — Deprecated, replaced by context_writer.py (609 lines)
- **`exhaustion_detector.py`** — Never worked (wrong glob pattern, wrong file format parsing, 200K thresholds obsolete with 1M context)
- **`detect_speckit_milestone()`** in event_detector.py — Dead code (post_hook only runs for Bash, not Skill)
- **`SPECKIT_MILESTONE`** enum value from EventType
- **`test_config_loader.py`** — Tests for deleted module
- **`test_discovery_classifier.py`** — Tests for deleted module
- Slow execution detection in subagent_stop.py (duration_ms always None)

#### Added
- **`test_cross_layer_consistency.py`** — 24 tests validating consistency between settings.json ↔ safe_commands ↔ blocked_commands ↔ tiers ↔ skills ↔ task_validator

#### Metrics
- Dead code removed: ~1,500 lines (config_loader + discovery_classifier + exhaustion_detector + dead test files)
- All 890 tests pass, 0 failures

## [3.12.0] - 2026-02-17

### Refactor: Principle-First Skills & Agent Deduplication

Major redesign of skills and agents. Skills now teach principles instead of enumerating commands. Agents delegate process knowledge to skills, keeping only domain identity.

#### Removed
- **`skills/anti-patterns/`** - Merged into `command-execution` skill as defensive execution principles

#### Changed
- **`skills/command-execution/SKILL.md`** - Complete rewrite with defensive execution framework
  - Timeout hierarchy (tool-native → shell wrapper → abort)
  - Pre-flight checklist ("Can this hang?" / "Do I know the timeout?")
  - 7 numbered rules: no pipes, one command per step, Claude Code tools over bash, validate before mutate, absolute paths, files over inline data, quote variables
- **`skills/security-tiers/SKILL.md`** - Changed from command enumeration to decision framework
  - Classification by question: "Does it modify live state?" → T3
- **`skills/terraform-patterns/SKILL.md`** - Split into slim SKILL.md (86 lines) + reference.md
- **`skills/gitops-patterns/SKILL.md`** - Split into slim SKILL.md (94 lines) + reference.md
- **`skills/fast-queries/SKILL.md`** - Cut from 256 to 41 lines (essentials only)
- **`skills/investigation/SKILL.md`** - Fixed to use Glob/Grep/Read tools, removed duplicated content
- **`skills/output-format/SKILL.md`** - Removed dead escalation protocol
- **`skills/execution/SKILL.md`** - Consolidated commit format to git-conventions reference
- **`skills/approval/SKILL.md`** - Removed duplicated commit standards and AskUserQuestion section
- **All 6 agents** - Removed duplicated Before Acting, Investigation Protocol, Pre-loaded Standards, and command enumeration tier tables

#### Added
- **`skills/reference.md`** - Agent template and npm release checklist (moved from gaia agent)
- **`skills/terraform-patterns/reference.md`** - Full HCL examples
- **`skills/gitops-patterns/reference.md`** - Full YAML examples
- **`investigation` skill** assigned to cloud-troubleshooter, platform-architect, gitops-operator, devops-developer, gaia
- **`git-conventions` skill** assigned to platform-architect, gitops-operator, devops-developer
- **`agent-protocol` + `security-tiers` skills** assigned to speckit-planner

#### Metrics
- Skills: 1,865 → 725 lines (-61%)
- Agents: 1,914 → 1,007 lines (-47%)
- Total injected tokens significantly reduced
- All 882 tests pass

## [3.11.0] - 2026-02-16

### feat: 3-Layer E2E Testing System

Added Layer 1 prompt regression tests (86 tests) validating agent frontmatter, prompt content, skill cross-references, context contracts, security tier consistency, routing table, and skill content rules.

## [3.7.0] - 2026-01-20

### Refactor: Commit Validator Architecture

Moved commit validation to hooks system for better encapsulation and clearer separation of concerns.

#### Changed
- **commit_validator.py location**: Moved from `tools/validation/` to `hooks/modules/validation/`
- **bash_validator.py imports**: Updated to use relative import from sibling module
- **Module structure**: commit_validator.py now exclusively used by bash_validator.py (no direct imports)
- **Documentation**: Updated tools/validation/README.md to reflect new architecture

#### Technical Details
- bash_validator.py now uses relative import: `from ..validation.commit_validator import validate_commit_message`
- commit_validator.py path resolution updated for new location (4 dirname calls instead of 3)
- pre-publish-validate.js updated to validate new path
- tools/validation/__init__.py no longer exports commit_validator (internal use only)

#### Benefits
- Better encapsulation: commit validation only accessible through bash_validator
- Clearer architecture: validation logic properly contained within hooks system
- No breaking changes: commit validation continues to work identically

## [3.6.1] - 2026-01-20

### Fix: Include skills/ directory in npm package

#### Fixed
- **package.json files array**: Added `"skills/"` to ensure skills directory is published to npm
- This was preventing skills/standards/ from being available in v3.6.0

## [3.6.0] - 2026-01-20

### Standards Migration to Skills System

Major architectural change: migrated from dual context system (standards + skills) to unified skills-based architecture.

#### Added
- **New skills directory**: `skills/standards/` with 4 standards skills:
  - `security-tiers/` - T0-T3 operation classification (auto_load)
  - `output-format/` - Global output contract for all agents (auto_load)
  - `command-execution/` - Shell security rules and timeout guidelines (triggered)
  - `anti-patterns/` - Common mistakes by tool: kubectl, terraform, gcloud, helm, flux, npm, docker (triggered)
- **Standards loader in skill_loader.py**: New `_load_standards_skills()` method
- **Standards config in skill-triggers.json**: New `standards` section with auto_load and triggers

#### Changed
- **Unified loading system**: All context now loaded via `skill_loader.py` (skills only)
- **skill-triggers.json**: Added `standards` section with 4 skills configuration

#### Removed
- **build_standards_context()**: Removed 91 lines from `context_provider.py`
- **Standards system**: Deleted `get_standards_dir()`, `read_standard_file()`, `should_preload_standard()`, `build_standards_context()`
- **--no-standards flag**: Removed from context_provider.py (no longer needed)
- **docs/ directory**: Eliminated symlink `.claude/docs` (standards now in skills/)
- **Obsolete tests**: Removed 66 lines of standards-specific tests from `test_context_provider.py`
- **Duplicate content**: Removed docs/standards reference from universal-protocol skill

#### Migration Notes
- **Breaking change**: Systems relying on `.claude/docs/standards/` must update to use skills system
- **Skills auto-load**: `security-tiers` and `output-format` now load for ALL agents (not just PROJECT_AGENTS)
- **No functional impact**: Same content, different delivery mechanism
- **Benefits**: Single loading system, better versioning, no duplication

## [3.3.2] - 2025-12-11

### Read-Only Auto-Approval & Code Optimization

Major improvements to the permission system with compound command support and code quality optimizations.

#### Added
- **Compound command auto-approval**: Safe compound commands (`cat file | grep foo`, `ls && pwd`, `tail file || echo error`) now execute WITHOUT ASK prompts
- **Extended safe command list**: Added `base64`, `md5sum`, `sha256sum`, `tar`, `gzip`, `time`, `timeout`, `sleep` to always-safe commands
- **Multi-word command support**: Added `kubectl get/describe/logs`, `helm list/status`, `flux check/get`, `docker ps/images`, `gcloud/aws describe/list` as always-safe

#### Changed
- **R1: Unified safe command configuration** (`SAFE_COMMANDS_CONFIG`) - Single source of truth for all safe commands, eliminating ~150 lines of duplicate patterns
- **R2: Unified validation flow** - `classify_command_tier()` now uses `is_read_only_command()` for T0 classification
- **R4: Singleton ShellCommandParser** - Single instance reused across all validations

#### Removed
- **R3: Dead code removal** - Removed unused `_contains_command_chaining()` method (~30 lines)
- **Removed tenacity dependency** - Simplified capabilities loading (retry logic was over-engineering)
- **Removed duplicate `allowed_read_operations`** - Now derived from `SAFE_COMMANDS_CONFIG`

#### Fixed
- Compound commands with safe components no longer trigger ASK prompts
- More consistent tier classification between auto-approval and security validation

#### Technical Details
- **Lines reduced**: ~200 lines removed through deduplication
- **Maintainability**: Single source of truth for safe commands
- **Performance**: Singleton parser avoids repeated instantiation

#### Test Results
All previous tests continue to pass:
- Simple read-only commands: NO ASK (auto-approved)
- Safe compound commands: NO ASK (NEW - auto-approved)
- Dangerous commands: BLOCKED correctly
- Compound with dangerous components: BLOCKED correctly

---

## [3.3.1] - 2025-12-11

### Granular AWS Permissions & Command Chaining Block

Refined AWS permission patterns to read-only operations and blocked command chaining to ensure predictable permission evaluation.

#### Changed
- **AWS permissions**: Replaced broad service wildcards with granular read-only patterns
  - `Bash(aws ec2:*)` → 40 specific `describe-*` and `get-*` commands
  - `Bash(aws s3:*)` → `s3 ls`, `s3api get-*`, `s3api list-*`, `s3api head-*`
  - `Bash(aws rds:*)` → `describe-*`, `list-tags-for-resource`
  - `Bash(aws iam:*)` → `get-*`, `list-*`, `generate-*`, `simulate-*`
  - Similar granular patterns for Lambda, Logs, CloudWatch, CloudFormation, ELB, Route53, SecretsManager, SSM, SNS, SQS, DynamoDB, ECR, EKS, ElastiCache

#### Added
- **Command chaining block** in `pre_tool_use.py`:
  - Blocks `&&`, `;`, `||` operators to prevent bypassing permission checks
  - Allows pipes `|` (don't affect permissions)
  - Smart detection avoids false positives in quoted strings
  - Clear error message: "Execute each command separately"

#### Fixed
- Moved `agents/README.md` files to `docs/` to resolve Claude Code parse errors

#### Security Impact
- Modification commands (create, start, stop) now properly require ASK confirmation
- Chained commands can no longer bypass individual permission evaluation
- Read-only operations execute without confirmation

---

## [3.2.3] - 2025-12-09

### Service-Level Permission Wildcards

Simplified permission patterns using service-level wildcards for better Claude Code compatibility.

#### Changed
- **AWS patterns**: Simplified from `Bash(aws rds describe-:*)` to `Bash(aws rds :*)`
  - Service-level wildcards: `aws ec2`, `aws rds`, `aws s3`, `aws iam`, etc.
  - Works around Claude Code pattern matching issues with hyphens
- **GCP patterns**: Simplified to `Bash(gcloud compute :*)`, `Bash(gcloud container :*)`, etc.
- **Format standardization**: Removed spaces before `:*` for commands without arguments

#### Fixed
- Agent README files renamed back to `README.md` (underscore prefix removed)
- Pattern matching now works for `aws rds describe-db-instances` and similar commands

#### Impact
- **Read-only commands**: Execute automatically ✓
- **Modification commands** (start/stop, upload, resize): Now execute automatically (Option A1)
- **Destructive commands** (delete, terminate): Still blocked ✓

#### Philosophy (Option A1 - Permissive with guardrails)
- Wide `allow[]` for entire services (e.g., `aws ec2 :*`)
- Strict `deny[]` for destructive operations
- Trade-off: Modification commands no longer require confirmation

---

## [3.2.2] - 2025-12-09

### Enhanced Permissions System

Complete overhaul of the permissions configuration to implement "permissive-with-guardrails" strategy.

#### Changed
- **Comprehensive allow[] rules**: 331 specific read-only patterns for shell, git, kubernetes, helm, flux, terraform, aws, gcp, docker commands
- **Granular ask[] rules**: 162 modification operations that require user confirmation
- **Strict deny[] rules**: 73 destructive operations that are completely blocked

#### Fixed
- Removed duplicate patterns (`uname:*`, `xargs:*`)
- Fixed `gsutil rm -r:*::*` → `gsutil rm -r:*` (incorrect double colon)
- Added missing `git branch:*` to allow[] for `git branch -a`

#### Added
- **New test suite**: `tests/permissions-validation/test_permissions_validation.py`
  - Emulates Claude Code's actual permission matching behavior
  - 114 test cases across 13 categories
  - Tests prefix matching with `:*` wildcard
  - Validates precedence: Deny → Allow → Ask

#### Philosophy
- **Allow**: Read-only commands execute automatically (no confirmation)
- **Ask**: Modification commands require user approval (can be approved)
- **Deny**: Destructive commands are blocked (cannot be approved)

---

## [3.2.1] - 2025-12-06

### Security Fix - Permission Bypass Bug

**Critical security fix** for permission enforcement in `settings.template.json`.

#### Fixed
- **Removed generic `"Bash"` from `allow[]`**: The generic `"Bash"` permission was bypassing all specific `ask[]` rules like `"Bash(git push:*)"`, allowing T3 operations (git push, git commit) to execute without user confirmation.
- **Changed hook matcher from `"BashTool"` to `"Bash"`**: The PreToolUse and PostToolUse hooks were configured with matcher `"BashTool"` but Claude Code invokes the tool as `"Bash"`, causing hooks to never execute.

#### Root Cause Analysis
- See post-mortem: Generic permission `allow: ["Bash"]` has higher precedence than specific `ask: ["Bash(git push:*)"]` in Claude Code's permission evaluation.
- Hook matchers must match the exact tool name used by Claude Code.

#### Impact
- All git operations (push, commit, add) now correctly trigger "ask" confirmation
- PreToolUse hooks now execute for bash commands
- Security tier enforcement restored

---

## [3.2.0] - 2025-12-06

### Added - Episodic Memory P0+P1 Enhancements

Inspired by [memory-graph](https://github.com/gregorydickson/memory-graph) analysis, selective feature adoption.

- **P0: Outcome Tracking** (`tools/4-memory/episodic.py`)
  - New fields: `outcome`, `success`, `duration_seconds`, `commands_executed`
  - Valid outcomes: "success", "partial", "failed", "abandoned"
  - New method: `update_outcome()` - Update episode results after execution
  - Search boost: 10% relevance increase for successful episodes

- **P1: Simple Relationships** (`tools/4-memory/episodic.py`)
  - New field: `related_episodes` - List of related episode IDs with types
  - Relationship types: SOLVES, CAUSES, DEPENDS_ON, VALIDATES, SUPERSEDES, RELATED_TO
  - New method: `add_relationship()` - Link episodes together
  - New method: `get_related_episodes()` - Query related episodes (outgoing/incoming/both)
  - Search enhancement: `include_relationships=True` parameter

- **Statistics Enhancements**
  - Outcome counts by type
  - Total relationships count
  - Relationship types breakdown

- **CLI Commands**
  - `store --outcome --duration` - Store with outcome tracking
  - `update-outcome <id> <outcome>` - Update episode outcome
  - `add-relationship <source> <target> <type>` - Create relationship
  - `get-related <id>` - Query related episodes
  - `search --include-relationships` - Search with relationship context

### Design Decisions

- Backward compatible: All new fields optional with None defaults
- Audit trail: Relationship and outcome events logged to JSONL
- Performance limits: 1000 episodes, 5000 relationships in index
- No external dependencies: Pure Python implementation

## [3.1.1] - 2025-12-06

### Fixed

- **package.json** - Added `docs/` to files array (was missing in 3.1.0)
  - `docs/standards/` now included in npm package
  - Required for hybrid pre-loading in `context_provider.py`

## [3.1.0] - 2025-12-06

### Added - Token Optimization & Consolidation

- **NEW:** `docs/standards/` - Shared execution standards
  - `security-tiers.md` - T0-T3 definitions
  - `output-format.md` - Report structure
  - `command-execution.md` - Execution pillars
  - `anti-patterns.md` - Common mistakes by tool

- **NEW:** Hybrid pre-loading in `context_provider.py`
  - Always loads: security-tiers, output-format
  - On-demand: command-execution
  - **78% token reduction** per agent invocation

- **NEW:** QuickTriage scripts
  - `tools/fast-queries/cloud/aws/quicktriage_aws_troubleshooter.sh`
  - `tools/fast-queries/appservices/quicktriage_devops_developer.sh`

### Changed - Agent Optimization

- **agents/*.md** - All 6 agents reduced by 78%
  - platform-architect: 916 → 183 lines
  - gitops-operator: 1,238 → 217 lines
  - gcp-troubleshooter: 600 → 156 lines
  - aws-troubleshooter: 565 → 142 lines
  - devops-developer: 641 → 173 lines

### Removed - Session System Consolidation

- **REMOVED:** Session management system (consolidated into Episodic Memory)
  - `commands/save-session.md`
  - `commands/restore-session.md`
  - `commands/session-status.md`
  - `hooks/session_start.py`
  - `tools/5-task-management/session-manager.py`
  - `tools/5-task-management/create_current_session_bundle.py`
  - `tools/5-task-management/restore_session.py`

### Changed - Episodic Memory Enhanced

- **tools/4-memory/episodic.py** - Added `capture_git_state()` migrated from session system

### Fixed - Test Suite

- **359 tests passing (100%)**
- Fixed import in `test_commit_validator.py`
- Fixed import in `test_episodic_memory.py`
- Updated `test_agent_definitions.py` for meta-agents
- Changed `test_hook_blocks_docker_ps` to `test_hook_default_permit_for_docker_ps`
- Fixed 11 warnings (return → assert)

### Changed - Documentation

- **README.md & README.en.md** - Updated to v3.1.0, reduced 41%
- **All subdirectory READMEs** - Reduced 63% total (~2,025 lines removed)
- Eliminated all references to session system

---

## [3.0.0] - 2025-12-05

### Added - Agent Intelligence System (MAJOR)

- **NEW:** `tools/10-agent-intelligence/` module for intelligent agent optimization
  - `agent_writing_assistant.py` (24KB) - Assists in writing and improving agent definitions
  - `workflow_optimizer.py` (29KB) - Applies the 7 LLM Engineering Principles to optimize workflows
    - Binary Decision Trees
    - Guards Over Advice
    - Tool Contracts
    - Failure Paths
    - TL;DR First
    - References Over Duplication
    - Metrics Over Subjective Goals

- **NEW:** `tools/4-memory/` Episodic Memory System
  - `episodic.py` (23KB) - Persistent storage and retrieval of historical context
  - `demo.py` - Demonstration script for episodic memory
  - Features:
    - Automatic episode storage with keywords and classifications
    - Smart search with time decay and relevance scoring
    - Auto-classification of episode types (deployment, troubleshooting, etc.)
    - Index management with automatic trimming (1000 episode limit)
    - Audit trail with append-only JSONL file

- **NEW:** `tools/conversation/` Enhanced Conversation Management
  - `enhanced_conversation_manager.py` (21KB) - Advanced conversation state management
  - `agent_contract_builder.py` (19KB) - Dynamic agent contract generation
  - `progressive_disclosure.py` (17KB) - Progressive context disclosure for token optimization

- **NEW:** `tests/workflow/` directory for workflow-specific tests
- **NEW:** `tests/test_agent_contract_integration.py` - Agent contract validation tests
- **NEW:** `tools/agent_capabilities.json` - Centralized agent capabilities definition

### Changed - Agent Enhancements

- **agents/gaia.md** - Major refactoring (1707 lines changed)
  - Streamlined agent definition
  - Improved protocol definitions
  - Better integration with new intelligence modules

- **agents/gitops-operator.md** - Enhanced with 234 new lines
  - Improved Kubernetes operation patterns
  - Better Flux CD integration guidance
  - Enhanced troubleshooting protocols

- **agents/platform-architect.md** - Enhanced with 47 new lines
  - Improved Terragrunt support
  - Better module design guidance
  - Enhanced security scanning protocols

- **agents/gcp-troubleshooter.md** - Enhanced with 52 new lines
  - Improved GKE diagnostics
  - Better IAM analysis patterns
  - Enhanced networking troubleshooting

### Changed - Tools & Infrastructure

- **hooks/pre_tool_use.py** - Major enhancement (286+ lines)
  - Improved security validations
  - Better command blocking logic
  - Enhanced credential detection

- **hooks/subagent_stop.py** - Enhanced with 193 new lines
  - Better result packaging
  - Improved bundle generation
  - Enhanced session integration

- **tools/2-context/context_provider.py** - Enhanced (120+ lines changed)
  - Better provider detection
  - Improved contract validation
  - Enhanced error handling

- **tools/3-clarification/workflow.py** - Major enhancement (162+ lines)
  - Episodic memory integration
  - Improved ambiguity detection
  - Better context enrichment

- **tools/9-agent-framework/agent_orchestrator.py** - Enhanced (38+ lines)
  - Better phase management
  - Improved error recovery
  - Enhanced logging

### Changed - Fast Queries (Simplified)

- **tools/fast-queries/README.md** - Simplified documentation (185 lines changed)
- **tools/fast-queries/run_triage.sh** - Streamlined (152 lines changed)
- **tools/fast-queries/terraform/quicktriage_terraform_architect.sh** - Enhanced (90+ lines)
- **tools/fast-queries/gitops/quicktriage_gitops_operator.sh** - Enhanced (69+ lines)
- **tools/fast-queries/cloud/gcp/quicktriage_gcp_troubleshooter.sh** - Enhanced (99+ lines)

### Removed (BREAKING)

- **REMOVED:** `tools/fast-queries/USAGE_GUIDE.md` (369 lines) - Consolidated into README
- **REMOVED:** `tools/fast-queries/appservices/quicktriage_devops_developer.sh` (38 lines)
- **REMOVED:** `tools/fast-queries/cloud/aws/quicktriage_aws_troubleshooter.sh` (45 lines)

### Improved

- **Token Efficiency:** New progressive disclosure system reduces context by up to 70%
- **Agent Intelligence:** Workflows now validated against 7 engineering principles
- **Memory System:** Historical context improves routing accuracy over time
- **Conversation Management:** Multi-turn conversations with intelligent context carry-over
- **Test Coverage:** New workflow and integration tests

### Migration Guide for v3.0.0

**Breaking Changes:**
1. Removed `quicktriage_devops_developer.sh` - Use agent directly
2. Removed `quicktriage_aws_troubleshooter.sh` - Use agent directly
3. Removed `USAGE_GUIDE.md` - See README.md instead

**New Features to Adopt:**
```python
# Episodic Memory
from tools.4_memory.episodic import EpisodicMemory
memory = EpisodicMemory()
memory.store_episode(prompt="...", context={...})

# Workflow Optimizer
from tools.10_agent_intelligence.workflow_optimizer import WorkflowOptimizer
optimizer = WorkflowOptimizer()
result = optimizer.analyze(workflow_content)

# Enhanced Conversation
from tools.conversation.enhanced_conversation_manager import EnhancedConversationManager
manager = EnhancedConversationManager()
```

**Recommended Actions:**
- Review new agent definitions for improved patterns
- Enable episodic memory for better context over time
- Use workflow optimizer to validate custom workflows

---

## [2.6.2] - 2025-11-14

### Added - Absolute Paths Support

- **NEW:** `normalizePath()` function - Handles both absolute and relative paths transparently
- **NEW:** CLI option `--project-context-repo` - Specify git repository for project context in non-interactive mode
- **NEW:** Environment variable `CLAUDE_PROJECT_CONTEXT_REPO` - Alternative way to specify context repo

### Changed

- **`getConfiguration()`** - Now normalizes paths using `normalizePath()`
- **`validateAndSetupProjectPaths()`** - Enhanced to handle absolute paths correctly
- **CLI help and documentation** - Updated examples with absolute paths

### Improved

- Path handling is now more robust and user-friendly
- Better error messages for path-related issues
- Clearer documentation and examples

### Examples

```bash
# Absolute paths without context repo
npx gaia-init --non-interactive \
  --gitops /home/user/project/gitops \
  --terraform /home/user/project/terraform \
  --app-services /home/user/project/services

# Absolute paths with context repo
npx gaia-init --non-interactive \
  --gitops /path/to/gitops \
  --terraform /path/to/terraform \
  --project-context-repo git@bitbucket.org:org/repo.git
```

---

## [2.3.0] - 2025-11-11

### Added - Phase 0 Clarification Module

- **NEW:** `tools/clarification/` module for intelligent ambiguity detection before routing
  - `clarification/engine.py`: Core clarification engine (refactored from clarify_engine.py)
  - `clarification/patterns.py`: Ambiguity detection patterns (ServiceAmbiguityPattern, NamespaceAmbiguityPattern, etc.)
  - `clarification/workflow.py`: High-level helper functions for orchestrators (`execute_workflow()`)
  - `clarification/__init__.py`: Clean public API
- **Protocol G** in `agents/gaia.md`: Clarification system analysis and troubleshooting guide
- **Rule 5.0.1** in `templates/CLAUDE.template.md`: Phase 0 implementation guide with code examples
- **Phase 0 integration** in `/speckit.specify` command
- **Regression tests** in `tests/integration/test_phase_0_regression.py`
- **Clarification metrics** to Key System Metrics (target: 20-30% clarification rate)

### Changed - Module Restructuring (BREAKING)

- **BREAKING:** `clarify_engine.py` and `clarify_patterns.py` moved to `clarification/` module
  - **Old imports:** `from clarify_engine import request_clarification`
  - **New imports:** `from clarification import execute_workflow, request_clarification`
- Updated `application_services` structure in project-context.json:
  - Added `tech_stack` field (replaces `technology`)
  - Added `namespace` field for service location
  - **Removed** `status` field (dynamic state must be verified in real-time, not stored in SSOT)
- Service metadata now shows only static information: `tech_stack | namespace | port`

### Fixed

- Import paths in `tests/tools/test_clarify_engine.py` updated to new module structure
- Service metadata test updated to reflect removal of dynamic status field
- All 20 unit tests passing with new module structure

### Migration Guide for v2.3.0

```python
# Before (v2.2.x)
from clarify_engine import request_clarification, process_clarification

# After (v2.3.0)
from clarification import execute_workflow

# Simple usage
result = execute_workflow(user_prompt)
enriched_prompt = result["enriched_prompt"]
```

---

## [2.2.3] - 2025-11-11

### Fixed - Deterministic Project Context Location

- **context_provider.py**
  - Always reads `.claude/project-context/project-context.json` (no fallback to legacy paths)
  - Removed legacy auto-detection logic and unused imports
  - Prevents "Context file not found" errors when projects only use the new structure
- **templates/CLAUDE.template.md**
  - Rule 1 clarifies when to delegate vs. self-execute
  - Rule 2 explicitly documents the `context_provider.py --context-file .claude/project-context/project-context.json …` invocation
  - Workflow summary now references orchestration docs after the table (cleaner render)

### Changed - CLI Documentation & Version Alignment

- **README.md / README.en.md**
  - Documented the exact `npx` commands (`npx gaia-init` / `npx @jaguilar87/gaia-ops`) and clarified installation steps
  - Updated "Current version" badges to **2.2.3**
- **package.json**
  - Bumped package version to `2.2.3`

### Benefits

- No manual tweaks needed to point `context_provider.py` at the correct project context
- CLAUDE template now tells the orchestrator exactly how to invoke the context provider
- README instructions reflect the real CLI entry points, reducing confusion for new installs

---

## [2.2.2] - 2025-11-11

### Added - Pre-generated Semantic Embeddings

- **NEW:** Included pre-generated intent embeddings in package (74KB total)
  - `config/intent_embeddings.json` (55KB) - Semantic vectors for intent matching
  - `config/intent_embeddings.npy` (19KB) - Binary embeddings for fast loading
  - `config/embeddings_info.json` (371B) - Metadata about embeddings

### Changed - Semantic Routing Now Works Out-of-the-Box

- **Semantic matching enabled by default:** No manual setup required
- **Routing accuracy improved:** Ambiguous queries now route correctly using semantic similarity
- **Example improvement:**
  ```
  Query: "puede decirme el estado de los servicios de tcm?"
  Before: devops-developer (keyword "ci" - incorrect)
  After: gitops-operator (semantic matching - correct)
  ```

### Fixed - Directory Structure Consistency

- **Consolidated `configs/` into `config/`:** All configuration and data files now in single directory
- **Updated tool references:**
  - `tools/semantic_matcher.py`: Updated embeddings path (configs/ → config/)
  - `tools/generate_embeddings.py`: Updated output path (configs/ → config/)
  - All documentation updated to reference correct paths

### Fixed - Test Suite (254 tests, 100% passing)

- **tests/system/test_configuration_files.py:**
  - Updated to validate `templates/settings.template.json` (package contains template, not installed settings.json)
  - Tests now reflect npm package structure instead of installed project structure

- **tests/system/test_directory_structure.py:**
  - Completely rewritten for npm package validation
  - Tests now verify package directories (agents/, tools/, config/, templates/, bin/)
  - Removed tests for installed-project structure (session/, .claude/ name)
  - Added comprehensive tests for all package subdirectories (agents, tools, hooks, config, speckit)

- **tests/tools/test_clarify_engine.py:**
  - Fixed import paths (tests/tools → gaia-ops/tools)
  - Made emoji checks flexible (accepts any emoji, not just 📦)
  - All 32 clarify_engine tests now pass

- **tests/tools/test_context_provider.py:**
  - Updated troubleshooter contract test (application_services is optional, not required)
  - Fixed invalid_agent test expectation (now correctly exits with code 1)

- **tools/context_provider.py:**
  - Changed behavior for invalid agents: now exits with code 1 (was: warning + empty contract)
  - Better error messages: "ERROR: Invalid agent" instead of "Warning: No contract found"

### Benefits

- Zero configuration: Semantic routing works immediately after installation
- Better routing: Handles ambiguous queries with 6x higher confidence
- Consistent structure: All config files in one place (`config/`)
- Smaller package: Embeddings optimized for size (74KB vs 5MB unoptimized)
- Regeneration optional: Users can regenerate with `python3 .claude/tools/generate_embeddings.py` if needed
- Test coverage: 254 tests passing (0 failures)

---

## [2.2.1] - 2025-11-10

### Fixed - Documentation Consistency

- **README.md & README.en.md:**
  - Updated version numbers from 2.1.0 → 2.2.0
  - Corrected package structure (hooks/, templates/, commands/)
  - Fixed hooks/ listing: now shows actual Python files (pre_tool_use.py, post_tool_use.py, etc.) instead of non-existent pre-commit
  - Fixed templates/ listing: removed non-existent code-examples/, listed actual files (CLAUDE.template.md, settings.template.json)
  - Added context-contracts.gcp.json and context-contracts.aws.json to config/ section
  - Removed CLAUDE.md and AGENTS.md from package root (only templates exist)
  - Added speckit/ directory to structure

- **config/AGENTS.md:**
  - Updated all references: `.claude/docs/` → `.claude/config/`
  - Fixed quick links and support documentation paths

- **config/agent-catalog.md:**
  - Updated all 5 context contract references: `.claude/docs/` → `.claude/config/`

- **index.js:**
  - Deprecated `getDocPath()` function with console warning
  - Function now redirects to `config/` directory instead of non-existent `docs/`
  - Added JSDoc @deprecated annotation

- **README.en.md (Documentation section):**
  - Removed broken reference to `./CLAUDE.md` (file not in package)
  - Fixed all documentation links: `./docs/` → `./config/`
  - Updated to match actual config/ directory structure

- **speckit/README.en.md:**
  - Removed 3 non-existent commands: speckit.clarify, speckit.analyze-plan, speckit.constitution
  - Updated command count: 9 → 7 actual commands
  - Removed references to non-existent tasks-richer.py tool
  - Removed entire sections for non-existent templates (data-model-template.md, contracts-template.md)
  - Updated tool files list with actual tools (task_manager.py, clarify_engine.py, context_provider.py)
  - Fixed all code examples to use only existing commands

- **tools/context_provider.py:**
  - Added auto-detection for project-context.json location
  - Honors GAIA_CONTEXT_PATH environment variable
  - Falls back through common locations (.claude/project-context.json, .claude/project-context/project-context.json)
  - Fixes agent routing failures when project-context.json is in non-legacy location

- **package.json:**
  - Fixed `npm test` script (was calling non-existent pytest tests)
  - Now echoes informative message about fixture availability

- **Agent Branding Unification:**
  - Renamed `agents/claude-architect.md` → `agents/gaia.md` (aligns with gaia-ops package name)
  - Renamed `commands/gaina.md` → `commands/gaia.md` (unified as `/gaia` command)
  - Updated all references in README.md, README.en.md, and agents/gaia.md
  - Complete branding consistency: package name, agent name, and command name all use "gaia"

### Benefits

- Accurate documentation: All paths and structures match actual package contents
- No broken links: References point to existing files
- Clear API: Deprecated functions clearly marked
- User trust: Documentation matches reality
- npm test passes: No false failures

---

## [2.2.0] - 2025-11-10

### Added - Unified Settings Template & Auto-Installation

- **NEW:** Created unified `templates/settings.template.json` (214 lines)
  - Merged functionality from `settings.json` + `settings.local.json`
  - Includes all hooks (PreToolUse, PostToolUse, SubagentStop)
  - Complete permissions (75+ allow, 9 deny, 27 ask entries)
  - Full security tier definitions (T0-T3)
  - Environment configuration

- **Auto-Installation:** `gaia-init.js` now automatically generates `.claude/settings.json`
  - Added `generateSettingsJson()` function
  - Integrated into installation workflow (Step 6.5)
  - Projects get complete settings from day 1

### Removed - Dead Code Elimination

- **CLAUDE.md** from package root (only template exists now)
- **templates/code-examples/** (321 lines - never imported or executed)
  - `commit_validation.py`
  - `clarification_workflow.py`
  - `approval_gate_workflow.py`
- **templates/project-context.template.json** (126 lines - unused, installer generates programmatically)
- **templates/project-context.template.aws.json** (128 lines - never used)
- **package.json:** Removed `CLAUDE.md` from files array

### Changed - Package Consistency

- **templates/CLAUDE.template.md:**
  - Updated all references: `.claude/docs/` → `.claude/config/`
  - Updated package name: `@aaxis/claude-agents` → `@jaguilar87/gaia-ops`
  - Removed code-examples reference (no longer exists)

- **README.en.md:**
  - Updated API examples to use `@jaguilar87/gaia-ops`
  - Changed `getDocPath()` → `getConfigPath()` (correct function)

- **index.js:**
  - Updated header and JSDoc comments with new package name
  - Updated example usage

- **agents/gaia.md:**
  - Updated system paths to reflect gaia-ops package structure
  - Clarified symlink architecture and layout

### Improved - Package Quality

- **Reduced template bloat by 57%:** 882 lines → 378 lines (504 lines removed)
- **Single source of truth:** One settings template instead of scattered config
- **Cleaner architecture:** Only actual templates remain in `templates/`
- **Better defaults:** Projects start with complete, production-ready settings

### Benefits

- Unified configuration: Everything in one settings.json file
- Automatic setup: No manual settings configuration needed
- Smaller package: 57% reduction in template code
- Flexibility maintained: Users can still create `settings.local.json` for overrides
- Package consistency: All references use correct package name

---

## [2.1.0] - 2025-11-10

### Added - Provider-Specific Context Contracts

- **NEW:** Created separate contract files per cloud provider
  - `config/context-contracts.gcp.json` - GCP-specific contracts
  - `config/context-contracts.aws.json` - AWS-specific contracts
  - Ready for `context-contracts.azure.json` (future)

- **Auto-Detection:** `context_provider.py` now automatically:
  1. Detects cloud provider from `metadata.cloud_provider`
  2. Falls back to inferring from field presence (`project_id` → GCP, `account_id` → AWS)
  3. Loads the correct contract file
  4. Validates against provider-specific requirements

- **Test Fixtures:** Added sample contexts for testing
  - `tests/fixtures/project-context.gcp.json`
  - `tests/fixtures/project-context.aws.json`

### Changed

- **Context Provider:** Updated `tools/context_provider.py`
  - Added `detect_cloud_provider()` function
  - Added `load_provider_contracts()` function
  - Updated `get_contract_context()` to accept provider contracts
  - Legacy contracts remain for backward compatibility

- **Field Names:** Standardized provider-specific fields
  - GCP: `project_details.project_id` (no change)
  - AWS: `project_details.account_id` (was `aws_account`)
  - Installer updated to generate correct field names

- **Templates:** Created AWS-specific template
  - `templates/project-context.template.aws.json`
  - Matches AWS naming conventions (EKS, RDS, ECR, etc.)

- **Documentation:** Updated `config/context-contracts.md`
  - Added "Provider-Specific Contracts" section
  - Documented how provider detection works
  - Explained benefits of provider-specific approach
  - Version bumped to 2.1.0

### Benefits

- Clarity: Field names match cloud provider terminology
- Simplicity: No complex conditional validation logic in agents
- Extensibility: Adding Azure = create one JSON file (15 minutes)
- Agents Stay Agnostic: Agents use pattern discovery, don't need provider logic
- Single Source of Truth: Orchestrator selects the right contract

### Backward Compatibility

- Legacy support maintained: If provider-specific contracts don't exist, falls back to hardcoded contracts
- Existing projects: Continue to work without changes
- Migration: Optional, but recommended for clarity

---

## [1.4.0] - 2025-11-10

### Changed - BREAKING: Complete Installer Redesign

- **NEW FLOW:** Directories first, context second (much more logical!)
  1. Ask for directories (gitops, terraform, app-services) - ALWAYS
  2. Ask for project context repo - OPTIONAL
  3. If NO context: Ask basic questions to create project-context.json
  4. If YES context: Use that configuration and done!

### Improved

- **Clearer Purpose:** Context repo is now clearly optional
- **Better Fallback:** If no context exists, creates a basic one with minimal info
- **All Fields Optional:** Can leave everything empty if you don't know yet
- **Logical Order:** Ask for what you always need first (paths), then optional context

---

## [1.3.6] - 2025-11-10

### Fixed

- **Installer:** Skip questions when project context already has the answers
- **Smart Detection:** Only ask what's missing or needs confirmation (paths)
- **User Experience:** Show config summary when context is loaded
- **Directory Creation:** Auto-create missing directories without prompting

### Changed

- When project context loads successfully, only asks to confirm/adjust paths
- Cloud provider, credentials, region, and cluster name auto-applied from context
- Clearer feedback showing what was loaded from project context
- Missing directories (gitops, terraform, app-services) now created automatically

---

## [1.3.5] - 2025-11-10

### Added

- **Smart Installer Flow:** Project context repo now asked FIRST, with auto-population of all config
- **Input Sanitization:** Handles "git clone <url>" pastes automatically (extracts just URL)
- **Auto-Configuration:** Parses project-context.json and pre-fills all wizard questions
- **Better Error Messages:** Clear troubleshooting tips for git clone failures (SSH keys, access, URL)

### Changed

- **Wizard Question Order:** Project context moved from last to first question
- **User Experience:** Reduced manual input when project context exists
- **Clone Strategy:** Validates project context early, then sets up in final location
- **Error Handling:** Installation continues even if project context clone fails

---

## [1.3.4] - 2025-11-10

### Fixed

- **Installer:** Removed incorrect AGENTS.md symlink creation in project root during installation
- **Documentation:** AGENTS.md now only accessible via `.claude/config/AGENTS.md` as intended
- **Package Quality:** Excluded Python cache files (`__pycache__/`) from published package

### Changed

- **README.md:** Updated project structure documentation to reflect correct AGENTS.md location
- **README.en.md:** Updated project structure and corrected package references
- **Package Size:** Reduced from 911.7 kB (93 files) to 660.7 kB (77 files) - 27% reduction

### Added

- **Package Metadata:** Added `homepage` and `bugs` fields to package.json for better npm discovery
- **Badges:** Added npm version, license, and Node.js version badges to README files
- **CI/CD:** Created GitHub Actions workflow for automated npm publishing
- **.npmignore:** Added file to exclude development artifacts from package
- **Cleanup Script:** Added `npm run clean` to remove Python cache files automatically
- **Pre-publish Hook:** Added `prepublishOnly` script for automatic cleanup before publishing

---

## Versioning Policy

### Version Number Format: MAJOR.MINOR.PATCH

- **MAJOR:** Breaking changes to orchestrator behavior (requires agent updates, system changes)
- **MINOR:** New features, sections, or substantial improvements (backward compatible)
- **PATCH:** Bug fixes, clarifications, typos (backward compatible)

### Examples

- Adding new agent: MINOR (e.g., 2.0.0 → 2.1.0)
- Changing core principle: MAJOR (e.g., 2.1.0 → 3.0.0)
- Fixing typo in docs: PATCH (e.g., 2.1.0 → 2.1.1)
- Refactoring structure (like 2.0.0): MAJOR (changed from monolith to modular)

---

## Maintainers

- **Primary:** Jorge Aguilar (jorge.aguilar87@gmail.com)
- **Contributors:** Claude Code Agent Swarm

---

## License

Internal documentation for Aaxis RnD team. Not for external distribution.
