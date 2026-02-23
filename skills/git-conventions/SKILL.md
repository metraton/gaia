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
- **Never use `git -C <path>`** — run git from the working directory directly. The permission system matches command prefixes; `git -C` breaks all `git <subcommand>:*` allow rules and triggers approval prompts for safe read-only operations.

## Hook Enforcement (Automatic)

- Format validation: type, scope, length, no period
- Claude Code footers auto-stripped transparently
- Blocked commands prevented at execution level
