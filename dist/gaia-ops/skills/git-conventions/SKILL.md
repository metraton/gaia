---
name: git-conventions
description: Use when creating a git commit or preparing changes for a pull request
metadata:
  user-invocable: false
  type: reference
---

# Git Conventions

## Commit Format

Conventional Commits format: `type(scope): description`

Structured commits enable automated changelogs, semantic versioning, and meaningful `git log` filtering. The `commit_validator.py` hook enforces this format automatically.

| Element | Rule |
|---------|------|
| Format | `type(scope): short description` |
| Types | feat, fix, refactor, docs, test, chore, ci, perf, style, build |
| Scope | Optional, reflects module/area changed |
| Subject | Max 72 chars, lowercase start, imperative mood, no period, no emoji |
| Body | Optional, blank line after subject, 72 char line wrap (warning) |
| Footers | `BREAKING CHANGE:`, `Refs:`, `Closes:`, `Fixes:` allowed |

## Examples

```
feat(helmrelease): add Phase 3.3 services
fix(pg-non-prod): correct API key environment variable mappings
refactor: simplify context provider logic
chore(deps): update terraform to v1.6.0
```

## Rules

- Use `git commit -m "type(scope): description"` format
- Do NOT add `Co-Authored-By` or `Generated with Claude Code` footers (hooks auto-strip these)
- Description starts lowercase, imperative mood
- **Never use git path flags** -- do not use `git -C <path>`, `git --git-dir=<path>`, or `git --work-tree=<path>`. The permission system matches command prefixes; these flags break all `git <subcommand>:*` allow/deny rules. Per `command-execution` Rule 2, run `cd` as a separate Bash call before running git commands.
- **Push to the feature branch by default.** Only push directly to `main` if explicitly instructed or the plan is already on main. Never force-push (`git push --force`).

## Hook Enforcement (Automatic)

The `commit_validator.py` hook validates against `config/git_standards.json`:

- **Forbidden footers** (error): `Co-Authored-By: Claude`, `Generated with Claude Code`, emoji-prefixed footers
- **Conventional Commits format** (error): must match `type(scope): description` with allowed types
- **Subject rules** (error): max 72 chars, no trailing period, no emoji
- **Body rules** (warning): blank line after subject, 72 char line wrap
