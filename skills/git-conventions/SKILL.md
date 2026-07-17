---
name: git-conventions
description: Use when creating a git commit or preparing changes for a pull request
---

# Git Conventions

## Commit Format

| Element | Rule |
|---------|------|
| Format | `type(scope): short description` |
| Types | feat, fix, refactor, docs, test, chore, ci, perf, style, build |
| Scope | Optional, reflects module/area changed |
| Subject | Max 72 chars, lowercase start, imperative mood, no period, no emoji |
| Body | Optional, blank line after subject, 72 char line wrap |
| Footers | `BREAKING CHANGE:`, `Refs:`, `Closes:`, `Fixes:`, `Implements:`, `See:` |

## Examples

```
feat(helmrelease): add Phase 3.3 services
fix(pg-non-prod): correct API key environment variable mappings
refactor: simplify context provider logic
chore(deps): update terraform to v1.6.0
```

## Git Path Flags

Target the repo with `git -C /absolute/path <verb>` -- this is the canonical
form in a subagent. The T3 consent gate parses `-C` as a flag and classifies
the command identically to the bare verb (`git -C /repo push` is T3 `push`,
same as `git push`); there is NO bypass of Gaia's gate. The full discipline --
why one byte-identical form prevents the approval loop, and why a separate `cd`
call cannot work (the cwd resets between Bash calls) -- lives in
`command-execution` Rule 7; follow it there.

Two narrow caveats:
- Prefer `-C /abs` (short flag) or `--git-dir=/abs` (equals form); both are
  cleanly absorbed. Avoid the SPACE-separated `--git-dir /abs` / `--work-tree
  /abs` forms -- the path leaks into the first non-flag token and shifts the
  subcommand the local-safe guard reads (a mutative verb like `push` is still
  caught by the fallback scanner, but commit-message-leak protection is lost).
- The historical warning that a leading path flag "bypasses all rules silently"
  described Claude Code's NATIVE settings.json prefix matching (`Bash(git
  commit:*)`), which Gaia used in 3.2.1 but no longer ships -- enforcement is
  now the PreToolUse hook, which is `-C`-robust. If a user adds their own
  `Bash(git ...:*)` allow rules, a leading path flag can still shift that
  prefix, but that affects only their auto-allow convenience, never Gaia's
  consent gate.

## Push Defaults

Push to the feature branch. Only push directly to `main` when explicitly
instructed or when the work is already on main. Force-push (`--force`)
requires explicit user instruction.

## Hook Enforcement

The `commit_validator.py` hook validates against standards inlined as
module-level constants in that file (`TYPE_ALLOWED`, `SUBJECT_MAX_LENGTH`,
`SUBJECT_RULES`, `BODY_MAX_LINE_LENGTH`) -- it covers the conventional-commits
format, subject, and body rules. Forbidden-footer detection lives separately
in `bash_validator` (hardcoded there). Format violations block the commit.
Body line length triggers warnings only.
