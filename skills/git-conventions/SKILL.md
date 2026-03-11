---
name: git-conventions
description: Use when creating a git commit or preparing changes for a pull request
metadata:
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
- **Never use git path flags** — do not use `git -C <path>`, `git --git-dir=<path>`, or `git --work-tree=<path>`. The permission system matches command prefixes; these flags break all `git <subcommand>:*` allow/deny rules. Instead, run `cd` as a **separate Bash call** before running git commands. Do NOT chain: `cd /path && git status` triggers manual approval. Two separate calls: first `cd /path`, then `git status`.
- **Push to the feature branch by default.** Only push directly to `main` if explicitly instructed or the plan is already on main. Never force-push (`git push --force`).

## Hook Enforcement (Automatic)

- Format validation: type, scope, length, no period
- Claude Code footers auto-stripped transparently
- Blocked commands prevented at execution level
