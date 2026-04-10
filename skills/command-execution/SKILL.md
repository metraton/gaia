---
name: command-execution
description: Use when executing any bash command, CLI tool, or shell operation
metadata:
  user-invocable: false
  type: discipline
---

# Command Execution

```
ONE COMMAND. ONE RESULT. ONE EXIT CODE.
NO PIPES. NO CHAINS. NO REDIRECTS.
```

## Mental Model

When you reach for a pipe, you have not looked for the flag yet.
CLIs have `--format`, `--filter`, `--limit` flags that do what pipes
do — without hiding exit codes or triggering extra permission prompts.

When you want to chain with `&&`, stop. Run one command, verify the
exit code, then run the next. Two verified commands beat one fragile chain.

For file I/O, always use Claude Code tools over Bash:

| Bash | Claude Code tool |
|---|---|
| `cat`, `head`, `tail` | Read |
| `echo >`, heredocs | Write |
| `sed -i`, `awk` | Edit |
| `grep -r`, `rg` | Grep |
| `find` | Glob |

## Rules

1. **No pipes** — find the CLI's native flag first.
2. **One command per step** — no `&&` or `;`.
3. **Tools over Bash** — for file I/O, always.
4. **Absolute paths** — agent cwd resets between calls; relative paths break silently.
5. **Quote variables** — unquoted `${VAR}` with spaces becomes multiple arguments.

## Traps

| If you're thinking... | The reality is... |
|---|---|
| "I'll pipe to grep/awk/jq to filter" | Find `--filter` or `--format` flag |
| "I'll chain with && for efficiency" | Run separately, verify each exit code |
| "Let me cat/head this file" | Use the Read tool |
| "Let me cd first, then run" | Use absolute path or `-chdir` |
| "I need jq to parse JSON" | Use `--format json` at source |
| "A heredoc is cleanest for multi-line" | Use Write tool. Heredocs fail in batch. |
| "This pipe is read-only, it's safe" | Pipes still hide exit codes |

**Exception:** `git commit -m "$(cat <<'EOF' ...)"` heredocs are allowed.

## Anti-Patterns

- `kubectl get pods | grep Error` → use `-l` label selectors or `--field-selector`
- `cd dir && terraform plan` → `terraform -chdir=/absolute/path plan`
- `cat file | wc -l` → Read tool

The `cloud_pipe_validator.py` hook enforces no-pipes at runtime.
For mutation rules and cloud CLI examples, see `reference.md`.
