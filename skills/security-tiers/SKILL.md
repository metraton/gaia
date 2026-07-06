---
name: security-tiers
description: Use when classifying any operation before executing it, or deciding whether user approval is required
metadata:
  user-invocable: false
  type: reference
---

# Security Tiers

security-tiers classifies every operation into four tiers so an agent knows whether it can run freely or must request the user's consent.

## The four tiers

| Tier | What it is | Approval? | Example verbs |
|------|------------|:---:|---------------|
| **T0** | Read-only; observes state, changes nothing | No | get, list, describe, show, logs, status |
| **T1** | Local validation; no remote calls, no state | No | validate, lint, fmt, check |
| **T2** | Simulation / dry-run; may read remote, never writes | No | plan, diff, dry-run, template |
| **T3** | State-mutating; creates, updates, or destroys | **Yes** | apply, create, delete, push, deploy |

`git commit` and `git add` are **not** T3 -- they are local-only operations (they touch the working tree and local refs, never remote state), so they classify as safe by elimination. Only `git push` mutates remote state and is T3. This matches `GIT_LOCAL_SAFE_SUBCOMMANDS` in `mutative_verbs.py`, where `commit` and `add` are listed as local-safe.

**T3 gates a direction, not a category of verb.** An operation needs consent because it moves the system toward *more* capability (it grants) or *less* recoverability (it destroys). An operation that only moves the other way -- that *reduces* capability already granted -- does not need consent, because the worst it can do is take back power that was given. So within Gaia's own consent layer, `gaia approvals revoke|reject|reject-all|clean` are **not** T3: they only revoke or discard grants Gaia itself issued, never reaching outside the local approval store. The asymmetry is deliberate -- `gaia approvals approve` *grants* capability without the AskUserQuestion flow, so it stays T3. This is anchored to the `gaia approvals` group in `CONSENT_REDUCING_SUBCOMMAND_EXCEPTIONS` (`mutative_verbs.py`), not generalized to every CLI's "revoke" -- a cloud IAM revoke is a real remote mutation and remains T3.

## Classification heuristic

Ask, in order -- the first "yes" wins:

1. **Does it mutate live state?** (create, update, delete, apply, push, deploy) -- **T3**
2. **Does it only simulate?** (plan, diff, dry-run, template) -- **T2**
3. **Does it validate locally?** (validate, lint, fmt, check) -- **T1**
4. **Is it read-only?** (get, list, describe, show, logs) -- **T0**

This mirrors `_classify_command_tier_cached` in `hooks/modules/security/tiers.py`: blocked patterns and mutative verbs resolve to T3 first, then simulation to T2, then validation to T1, and everything left over defaults to T0 -- safe by elimination, never by an allow-list.

Conditional commands depend on flags: `git branch` is T0 for listing but T3 with `-D`, `-d`, `-m`. For cloud-specific verb patterns (kubectl, terraform, gcloud, helm, flux), see `reference.md`.

## Enforcement anchors

The runtime, not this skill, enforces tiers. Three modules layer the decision:

- `tiers.py` -- the `SecurityTier` enum (`T0_READ_ONLY`, `T1_VALIDATION`, `T2_DRY_RUN`, `T3_BLOCKED`) and `_classify_command_tier_cached` assign every command a tier.
- `blocked_commands.py` -- pattern-matches irreversible commands and permanently denies them (exit 2, never approvable).
- `mutative_verbs.py` -- CLI-agnostic detection of mutative verbs; drives the nonce / approval flow for T3. Includes script-file detection (Step 1d, `_check_script_file`): when a command is `<interpreter> <script-file>` (`python3 deploy.py`, `bash setup.sh`, `node migrate.js`) or `./script.ext`, the file is read and classified by its real invocations -- AST analysis for Python, the blocked/mutative regex layer for shells and other interpreters. A script that is missing, unreadable, or whose interpreter is unrecognized defaults to T3 (conservative). This prevents the evasion path where `<interp> <file>` bypasses the verb scanner because the filename token has no recognizable subcommand. Before reading the body, `_check_script_file` first checks `_INTERP_SYNTAX_CHECK_FLAGS`: a leading syntax-check-only flag (`bash -n`, `sh -n`, `node --check` / `node -c`) that precedes the script positional never executes the script, so the invocation downgrades to T0 without reading the file's contents at all -- a flag appearing after the script positional is an argument to the script and does not qualify. Step 1e (`_check_npm_script_runner`) applies the same real-effect standard to npm: `npm run <script>` is resolved to its `package.json` `scripts.<script>` body and that body is classified by the same regex engine used for script files (an unresolvable body -- missing/unparseable `package.json` or absent entry -- falls back to conservative T3), while `npm ci` is unconditionally mutative (T3) because it rewrites `node_modules` regardless of the verb taxonomy. Non-shell source files (`.js`/`.mjs`/`.cjs`/`.rb`/`.pl`/`.php`) route through the **"code" lane** (`_classify_script_content_by_regex` with `from_source_code=True`), which suppresses camelCase subcommand splitting so a language identifier is not misread as a CLI verb (`execPath`/`execSync` -> `exec`, `setState` -> `set`, `stopPropagation` -> `stop`) -- whole-token verbs, command aliases (`rm`/`cp`), dangerous flags, and blocked-command patterns are still scanned. Because that suppression (and the quote making a command one token) would otherwise hide a mutation passed to a subprocess as a string literal, the code lane and the inline `-c`/`-e` path share one exec-sink detector (`_scan_exec_sink_string_args`): the command handed to an exec sink (`execSync`/`execFile`/`spawn`/`system`/`shell_exec`/`passthru`/backticks/`%x{}`) is extracted and re-classified, escalating to T3 **only when the inner command is itself mutative or blocked** (so `execSync("kubectl delete ...")` is T3 while a benign `execSync("ls")` stays T0 -- the false-positive gate). This makes `node deploy.js` classify identically to `node -e "..."`. Residual accepted-limitation: the general case -- a mutation assembled by string concatenation, variable interpolation, or base64, or passed to a sink not in the exec-sink set -- is not detected by static classification; the exec-sink slice is the bounded, low-false-positive portion that is closed.
- `composition_rules.py` -- `check_composition` / `classify_stage` classify pipe compositions (FILE_READ→EXEC_SINK, network→exec, decode→exec); triggers T3 on dangerous pipelines such as `file_to_exec`.
- `flag_classifiers.py` -- `_classify_curl` / `classify_by_flags` detect flag-dependent mutations; triggers T3 on commands whose flags make them mutative (e.g., `curl -X POST`).

Safe by elimination, with no allow-list: anything not blocked and not mutative is T0. Runtime is the single source of truth for nonce handling, grant scope, and approval enforcement -- this skill teaches how to think about the tier; it does not enforce it.

## The `.claude` rule

**Do not touch anything under `.claude/` -- ever, by any mechanism.** By Gaia core policy it is a hard security boundary. This is not a guideline to weigh against convenience; it is a precondition that must be satisfied before any operation begins.

**The rule applies to every execution path, not only deliberate edits.** A `sed -i`, `find -exec`, `xargs`, glob expansion, or any script that sweeps a directory tree is bound by exactly the same policy as a targeted `Edit` call. The mechanism does not change the obligation. If a bulk operation's scope *could* include a path whose components contain `.claude/` -- even deeply nested, such as `tests/fixtures/repo/.claude/settings.json` -- the correct sequence is:

1. Exclude those paths explicitly before running (e.g., `find . -path '*/.claude/*' -prune -o ...`), **or**
2. Do not run the operation at all, **or**
3. Ask the user first.

There is no fourth option. The policy is not "run it and let the hook decide" -- it is "do not attempt it." An agent that launches a bulk operation hoping the hook will catch `.claude/` paths has already violated the policy, regardless of whether the hook fires.

A second, deterministic layer backs the policy: even if attempted, the write cannot succeed. `_is_protected()` in `hooks/adapters/claude_code.py` hard-protects the most critical paths -- the Gaia hooks directory (which `.claude/hooks/` resolves into) and `settings.json` / `settings.local.json` anywhere under a `.claude/` path -- and it fires regardless of `permissionMode`. An agent running with `acceptEdits` is still blocked. The enforcement is unconditional and does not depend on the agent's intent or the operation's surface area.

Why state it here, at the top of tier classification: an agent that ignores the policy and tries anyway collides with the deterministic block. That surfaces as drift and confusing failures with no clear cause -- wasted cycles that do not produce a recoverable state. The policy is the prevention; the hook is the backstop. Knowing the rule before forming the intention spares that path entirely.

## T3 approval handoff

When a T3 command is blocked with an `approval_id`, emit `plan_status: APPROVAL_REQUEST` with the `approval_id` in `approval_request`, per the response envelope in `agent-protocol/SKILL.md`. See `subagent-request-approval/SKILL.md` for the full request schema.
