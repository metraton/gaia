---
name: git-conventions
description: Use when creating a git commit or preparing changes for a pull request
metadata:
  user-invocable: false
  type: reference
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

`git -C <path>`, `git --git-dir=<path>`, and `git --work-tree=<path>` break
the permission system. Allow/deny rules match command prefixes like
`git commit:*` -- path flags inserted before the subcommand shift the prefix
and bypass all rules silently. Run `cd` as a separate Bash call, then run git.

## Push Defaults

Push to the feature branch. Only push directly to `main` when explicitly
instructed or when the work is already on main. Force-push (`--force`)
requires explicit user instruction.

## Hook Enforcement

The `commit_validator.py` hook validates against `config/git_standards.json`.
Format violations block the commit. Body line length triggers warnings only.
