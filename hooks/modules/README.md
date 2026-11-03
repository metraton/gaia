# Hooks Modular Architecture

This directory contains the modular hook system for Gaia.

## What is this?

The logic of the Claude Code hooks, split into modules the entry points in `hooks/` and the adapters in `hooks/adapters/` import.

## Where does this fit?

```
Claude Code invokes hook
        |
        v
[session_start.py] --------> [modules/session/session_lifecycle] -> Registry, sweeps, birth block
        |
        v
[pre_tool_use.py] ---------> [modules/security/*] -> Tier classification
        |                     [modules/tools/*]   -> Bash/Task validation
        v
    Tool executes
        |
        v
[post_tool_use.py] --------> [modules/audit/*]    -> Logging & event detection
                              [modules/session/*]  -> Session context updates
                              [modules/agents/*]   -> Task result observation
```

`context/context_freshness.py` and `scanning/scan_trigger.py` have no caller: SessionStart scans no workspace.

## Module Structure

```
modules/
├── __init__.py           # Package marker
├── core/                 # Shared utilities
│   ├── paths.py          # find_claude_dir() - single source of truth
│   ├── plugin_setup.py   # Plugin channel's first-session settings merge (allow list, empty ask list, no deny rule)
│   └── state.py          # Pre/post hook state sharing
│
├── security/             # Classification, guards and approval
│   ├── tiers.py          # SecurityTier enum (T0-T3)
│   ├── verdict.py        # FREE / SIGNED / BLOCKED / UNKNOWN and the rule ids with their remediation
│   ├── blocked_commands.py # Permanently blocked patterns by category
│   ├── mutative_verbs.py   # CLI-agnostic verb detector; a T3 is denied with an approval_id
│   ├── nested_executors.py # kubectl/docker exec, op run, ... take the verdict of the command they run
│   ├── targets.py        # Where a command lands: context, project, account, branch; production detection
│   ├── canonical_path.py # One canonical form of a path for every path guard (Windows spellings included)
│   ├── gaia_cli_only_guard.py # The orchestrator's Bash lane: the trusted gaia binary and an allowlist of verbs
│   ├── gaia_db_write_guard.py # Refuses sqlite3 on gaia.db and shell writes to it (categorical); judges sqlite3's reach elsewhere
│   ├── subagent_memory_write_guard.py # Refuses `gaia memory` writes from subagents
│   ├── memory_skill_guard.py # Refuses the main session's memory writes until Skill('memory') is loaded
│   ├── subagent_resume_point_guard.py # Classifies `gaia session` per verb; refuses a subagent's session writes
│   ├── verifier_scratch_guard.py # Confines the verifier's Write to its own scratch entry
│   ├── protected_path_guard.py # Refuses Bash writes into the .claude/ tree and anywhere in an installed Gaia root (categorical)
│   ├── shell_write_guard.py # Refuses a shell-authored file write into a git working tree (categorical)
│   ├── sensitive_read_guard.py # Refuses reading or listing a path where secrets live (categorical)
│   ├── comment_push_gate.py # Refuses a `git push` of a branch over the comment limit (categorical, fails open with a warning)
│   ├── source_lexer.py     # Per-language comment/string lexer for the script lanes
│   ├── approval_grants.py  # Approval grant management
│   ├── approval_scopes.py  # Approval scope matching
│   └── command_semantics.py  # Command semantic analysis
│
├── tools/                # Tool-specific validators
│   ├── shell_parser.py   # Quote-aware split of compound commands and shell words
│   ├── bash_validator.py # Bash command validation (orchestrates the pipeline below); decide_t3_outcome
│   ├── task_validator.py # Task/Agent dispatch validation
│   ├── cloud_pipe_validator.py # Cloud pipe/redirect/chain check
│   └── hook_response.py  # Standardized hook response formatting
│
├── context/              # Context management
│   ├── kernel_builder.py # The subagent kernel, # Project context block included
│   ├── user_sections.py  # ## User memory for the birth block, and the shared context budget
│   ├── compact_context_builder.py # Instructions for the compaction summarizer
│   ├── context_writer.py # Writes project-context updates under the project each one names
│   └── context_freshness.py # Staleness check for SessionStart
│
├── session/              # Session lifecycle
│   ├── session_lifecycle.py # Host-neutral SessionStart core: registry, sweeps, the dead-turn reaper, the birth block
│   ├── session_manifest.py  # Assembles the birth block, the behind-gaia.db line included
│   └── session_registry.py  # Heartbeat-based registry of live sessions
│
├── events/
│   └── capability_usage.py # Records cli.invoked and skill.loaded harness events
│
├── validation/           # Commit validation
│   └── commit_validator.py # Conventional Commits enforcement
│
├── audit/                # Logging and metrics
│   ├── logger.py         # AuditLogger
│   ├── metrics.py        # MetricsCollector + generate_summary
│   └── event_detector.py # CriticalEventDetector
│
└── agents/               # Subagent support
    ├── dispatch_binding.py   # Born-at-dispatch row: extract + validate + birth the handoff row from dispatch metadata
    ├── dispatch_lifecycle.py # Dispatch row lifecycle, including reap_stale_turn
    ├── handoff_persister.py  # Persist/finalize the terminal agent_contract_handoffs row
    ├── contract_validator.py # Legacy fence parser (no closing role) + envelope field resolvers
    ├── skill_injection_verifier.py # Verify declared and inferred skills were loaded
    ├── call_ceiling.py       # Tool-call ceiling notice
    ├── state_tracker.py      # Legal agent_state transitions (_LEGAL_TRANSITIONS)
    ├── task_info_builder.py  # Build task_info from SubagentStop hook data
    └── transcript_reader.py  # Read the subagent transcript file
```

The tree names the modules a change most often touches; `git ls-files hooks/modules` lists all of them. Beside `modules/`, `hooks/mods/` holds the Claude Code mods the host loads in-process (`mods/compaction/register.ts`, named under `host_mods` in `build/gaia.manifest.json`).

### `dispatch_binding.py` (born-at-dispatch)

At dispatch time (PreToolUse:Agent / SubagentStart), the hook BIRTHS the
nascent `agent_contract_handoffs` row from the dispatch metadata, stamping the
four binding coordinates (`plan_task_id`, `plan_id`, `parent_handoff_id`,
`kind`) and validating their REFERENTIAL INTEGRITY before the row is born:

- `extract_dispatch_binding(metadata)` — best-effort parse of the dispatch
  prompt: `plan_id=`, `task_id=` (dropped for a verifier turn, which binds by
  parent), `parent_handoff_id=`, and `turn_role`/`kind` inferred from the
  target agent name.
- `validate_dispatch_binding(...)` — a `task_execution` kind requires a
  resolvable, dispatchable (`status='pending'`) `plan_task_id` whose plan is
  `active` (a draft or closed plan refuses it with
  `plan_task_id_plan_not_active`) and not paused (`gaia plan pause` refuses it
  with `plan_task_id_plan_paused` until `gaia plan resume`); a verifier turn
  (`turn_role='verifier'`) requires a resolvable `parent_handoff_id` (the
  producer handoff it verifies). `kind` is a pure label — never rejected for its
  value. Raises `DispatchBindingError` (with a machine-readable `reason`) when a
  coordinate does not resolve; the dispatch is never blocked.
- `birth_degraded_row(...)` — **birth is total.** Every `DispatchBindingError`
  degrades instead of dropping the row: the caller
  (`hooks/adapters/claude_code.py::_maybe_birth_dispatched_row`) births the row
  anyway with the unresolvable coordinate NULL and the rejection reason plus the
  failed token recorded inside the birth envelope, and the
  `dispatch.binding_rejected` anomaly event still fires. An unborn row is not a
  weaker binding but no contract at all — and an unrecoverable one, since
  `harness_agent_id` is stamped only at the SubagentStart claim and no CLI verb
  writes it.
- `birth_dispatched_row(...)` — validates the binding, then writes one
  `agent_state='DISPATCHED'` row via `gaia.store.writer.insert_dispatched_handoff`.
  Idempotent: a re-dispatch of the same `contract_id` never births a second row.

The finalize gate keys on this binding's `plan_task_id`
(`hooks/adapters/claude_code.py::_blind_verification_required`), not on the
emitting agent's role.

## Key Features

### Orchestrator Gate
The orchestrator's tools are the `tools:` list of `agents/gaia-orchestrator.md`: `Read`, `Bash`, `Agent`, `SendMessage`, `AskUserQuestion`, `Skill`, the `Task*` and `Cron*` tools, `WebSearch`, `WebFetch`, `ToolSearch` and `mcp__gaia__compact_when_idle`; `disallowedTools` removes `Glob`, `Grep`, `Edit`, `Write`, `NotebookEdit` and the plan and worktree modes. Its `Bash` is a lane, not a shell: `security/gaia_cli_only_guard.py` admits only the trusted `gaia` binary and an allowlist of verbs (`ALLOWED_READ_PHRASES`, `ALLOWED_WRITE_PHRASES`), and refuses everything else categorically. Domain work still goes to a specialist.

### SendMessage Validation (PreToolUse matcher)
SendMessage is validated as a PreToolUse event (not a separate hook event):
- Agent ID format check (must match `/^a[0-9a-f]{16,}$/`, imported from `gaia.contract.validator.AGENT_ID_PATTERN_TEXT` via `agents/response_contract.py` -- never re-spelled inline)
- Non-empty message required
- Grant activation is not part of this validation. It happens on PostToolUse, when the `AskUserQuestion` matcher reaches `adapters/claude_code.py::_handle_ask_user_question_result` and every answered label carrying a `[P-<nonce>]` tag activates its own grant

### Context Enforcement
A dispatched subagent receives its project context at SubagentStart, as the `# Project context` block of the kernel `context/kernel_builder.py` renders: the dispatch project's `can_read` sections, whole when they fit, else one `gaia context get-contract` pointer per section, else a count line.

### State Sharing
Pre-hook saves a `HookState` per `(session_id, tool_use_id)` under `.claude/.hooks_state/` (`.claude/.hooks_state.json` when either id is missing), which post-hook reads to get:
- Security tier assigned
- Command executed
- Timestamp for duration calculation

### Metrics
`generate_summary()` reads the audit logs and aggregates:
- Total executions
- Average duration
- Top command types
- Tier distribution
- Security events

## Usage

### Entry Points

The host runs each hook with the event JSON on stdin. The metrics summary:

```bash
gaia metrics
```

### Importing Modules

```python
from modules.security import SecurityTier, classify_command_tier, is_blocked_command
from modules.security import CATEGORY_MUTATIVE, CATEGORY_READ_ONLY
from modules.tools import BashValidator
from modules.audit import generate_summary

# Check if command is permanently blocked
result = is_blocked_command("kubectl delete namespace production")
print(f"Blocked: {result.blocked}, Reason: {result.reason}")

# Classify command tier
tier = classify_command_tier("kubectl get pods")
print(f"Tier: {tier}")  # SecurityTier.T0_READ_ONLY

# Validate Bash command (full pipeline: blocked -> mutative verbs -> safe by elimination)
validator = BashValidator()
result = validator.validate("kubectl get pods")
print(f"Allowed: {result.allowed}, Tier: {result.tier}")

# Get metrics summary
summary = generate_summary(days=7)
print(f"Executions: {summary['total_executions']}")
```

## Architecture Notes

The modular architecture maintains full backward compatibility with Claude Code's hook interface (stdin JSON format).

All security rules (blocked patterns, mutative verbs, tiers) are hardcoded in the Python modules for performance and simplicity - no external JSON config files needed.

### Validation Order (Defense-in-Depth)
`BashValidator.validate` checks commands in this order (short-circuit on first match):
0. **Orchestrator lane** (gaia_cli_only_guard.py) — for the orchestrator role only, ahead of everything else: the trusted `gaia` binary and its allowlisted verbs, or a categorical deny
0a. **Host consent verbs** (host_consent_verb_guard.py) — the OpenCode plugin's own consent verbs are refused from any shell; then a sealed retry of an approved command is recognized, and Claude footers are stripped
0b. **Categorical guards**, in this order — publish attribution, comment_push_gate.py, gaia_db_write_guard.py (the `sqlite3` CLI on gaia.db whatever its SQL, the store found by name, realpath or inode in the database argument or an `.open`/`ATTACH`/`VACUUM INTO`/file operand, a database argument it cannot resolve to a literal path refused as "could not prove", the denial naming `gaia context query`; on another database, a command or file sqlite3 reaches that the shell spelling would be refused; plus shell writers over the file), subagent_memory_write_guard.py (`gaia memory` writes from subagents), memory_skill_guard.py (main-session memory writes before `Skill('memory')`), subagent_resume_point_guard.py (a subagent's `gaia session` writes), contract ownership, protected_path_guard.py (Bash writes into the `.claude/` hooks/settings tree or an installed Gaia root; any line naming or running inside an install root must be all known readers, judged after substituting line assignments and well-known environment variables and expanding brace and glob words), shell_write_guard.py (shell-authored writes into a git working tree), sensitive_read_guard.py (secret reads) → exit 2, not approvable. A write target starting with a variable nothing resolves is T3 instead (`unprovable_write_target`), and so is a sqlite3 call on another database whose reach the guard cannot prove: a mutative or unknown `.shell` command, `-init`, `.read`, a `HOME` override, `.load`, `edit()`, a computed `writefile()` path (`sqlite_unproven_effect`, verb `sqlite-reach`). These guards split the line with the quote-aware `tools/shell_parser.py`, so an operator or a path inside quotes is text: a quoted CLI argument is data, not a command
1. **Indirect execution detection** — `bash -c`, `eval` and the other wrappers: a subagent is refused with the correct form; for the main session a wrapper whose inner command mutates goes on to the T3 gate, and one that does not asks the host
2. **Blocked commands** (blocked_commands.py) — permanently denied patterns, exit 2
3. **Commit message validation** — conventional commits enforcement
4. **Cloud pipe/redirect/chain check** (cloud_pipe_validator.py) — corrective deny, then pipe composition (composition_rules.py)
5. **Mutative verbs** (mutative_verbs.py) — every T3 converges on `bash_validator.py::decide_t3_outcome`: a deny carrying an `approval_id` and the `gaia approvals request-set` line, for a subagent and for the main session alike, never the host's permission prompt; a chain of several T3 commands is denied whole and asks for a plan-first request-set
6. **Everything else** — SAFE by elimination (auto-approved)

### Heredoc readers

Every `<<` on a line is paired with the command segment that reads it, and bodies are taken in the order the shell reads them (`_heredoc_openers`, `_scan_heredocs` in `tools/bash_validator.py`). A segment starts after `;`, `|`, `&`, `&&`, `||` or `(`.

A heredoc body is data only when that reader, after its wrappers (`sudo`, `env`, `timeout`, `nice`, `command`, `builtin`, `exec`), provably cannot execute or write what it reads (`_heredoc_reader`):

- **Pure data readers.** `cat`, `head`, `tail`, `wc`, `sort`, `uniq`, `tr`, `cut`, `nl`, `rev`, `fold`, `tee`, and `base64` encoding or decoding. A `tee` that writes a protected or account path is still refused or signed by the write guards, which read the command line.
- **`jq`**, even with `-f /dev/stdin`: a jq program cannot run a command.
- **`grep`/`egrep`/`fgrep` without `-f`/`--file`.**
- **Message flags**, keyed by program, subcommand path and flag (`_HEREDOC_MESSAGE_FLAGS`). Any other path, alias or extension (`gh x`, `gh myext run`, `gh api`) is code, because it may run stdin.

`awk`, `gawk`, `sed` and `yq` are not on the list, whatever their flags. Their languages run commands or write files (`system()`, sed's `e` and `w`, `print >`), so a heredoc they read fails closed even when it only feeds them input.

A heredoc that `ruby`, `perl` or `php` reads as its program asks: no parser here can prove what that code does.

`xargs` is never relief. Each body line is evaluated through the whole validator as `<target> <line>`, the command xargs would run (`_xargs_payload`, value flags such as `-d`, `-I` and `--arg-file` skipped). `xargs cat` with `~/.ssh/id_rsa` is refused as a secret read, `xargs tee` with `~/.bashrc` signs as an account write, and `xargs cat` with `notes.txt` runs free. Each line is checked in the caller's working directory, the one the hook payload carries, so `xargs tee` with `notes.txt` inside a git repository is refused exactly as `tee notes.txt` is there. When that directory is unknown, a line with a relative word (neither `/`, `~` nor `-` first) asks for a signature instead of resolving against the hook's own directory.

| Program | Subcommand path | Flag reading `-` |
|---------|-----------------|------------------|
| `git` | `commit`, `tag`, `notes` | `-F`, `--file` |
| `gh` | `pr` or `issue` + `create`, `edit`, `comment`; `release` + `create`, `edit` | `--body-file`, `--notes-file`, `-F` |
| `glab` | `mr` or `issue` + `create`, `update`, `note` | `--description` |

A data heredoc's command keeps its own verdict: `git commit -F - <<EOF` runs free, while `git tag -a v1 -F -` still signs for the tag. A data body is never read as commands, by the blocked-command floor, the secret-read guard, the cloud-pipe check and the kubectl secret-output check alike, so a commit message that names `kubectl delete ns prod` is not refused. Only a line of an unquoted body that holds `$` or a backtick is checked, because the shell expands it before the reader sees it. A data reader whose output is piped on (`cat <<EOF | sh`) keeps its body as commands, and so does one whose line ends in a backslash: the shell joins the next line (`| sh`) to the command before it reads the body.

Every other reader makes the body code, and the heredoc fails closed (`_fail_closed_on_code_heredoc`):

- Each body line is read as a shell command.
- A reading that hits a floor blocks the command. A mutative reading signs it, with the rule of its strongest effect.
- With neither, the command asks as UNKNOWN (`unknown.dynamic-span`). Gaia does not parse another language: an awk program, or Python behind `timeout` or `nice`, is code it cannot read.

One reader is read in full: an interpreter that takes its program from the heredoc with nothing before it (`python3 -`, `node -`, `bash -s`, bare `sh`; `security/program_heredoc.py`). Its body goes through the inline-code or script lanes like a script file, wherever the heredoc sits on the line (`_heredoc_as_program`). That holds only while the body is the program that runs. An unquoted delimiter whose body carries `$` or a backtick lets the shell rewrite the body first, so it asks as UNKNOWN too.

The list is short on purpose. A reader left off it over-asks, which is the cost of failing closed. A reader wrongly on it would run a body nobody classified.

### Tier Classification
- **T0**: Read-only (get, list, describe, show)
- **T1**: Local validation (validate, lint, fmt, check)
- **T2**: Simulation (plan, template, diff, --dry-run)
- **T3**: State-modifying (apply, delete, push); `git commit` and `git add` are local-only and free
