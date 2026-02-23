---
name: git-conventions
description: Conventional commits format and git workflow rules
user-invocable: false
---

# Git Conventions

## Commit Format

All commits MUST follow Conventional Commits: `type(scope): description`

```
type(scope): short description (max 72 chars, no period)
```

**Allowed types:** feat, fix, refactor, docs, test, chore, ci, perf, style, build

## Rules

- Use `git commit -m "type(scope): description"` format
- Do NOT add `Co-Authored-By` or `Generated with Claude Code` footers (hooks auto-strip these)
- Scope should reflect the module/area changed
- Description starts lowercase, imperative mood
- **Never use git path flags** — do not use `git -C <path>`, `git --git-dir=<path>`, or `git --work-tree=<path>`. The permission system matches command prefixes; these flags break all `git <subcommand>:*` allow/deny rules. Instead, `cd` to the repository root first and run git commands from there.

## Hook Enforcement (Automatic)

- Format validation: type, scope, length, no period
- Claude Code footers auto-stripped transparently
- Blocked commands prevented at execution level
