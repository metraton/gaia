---
name: command-execution
description: Use when executing any bash command, CLI tool, or shell operation
user-invocable: false
---

# Command Execution

```
NO PIPES. NO CHAINS. NO REDIRECTS.
One command. One result. One exit code.
```

## Mental Model

Every command you run is **atomic and self-contained**: inputs via flags, output to stdout, one exit code. You never pipe to reshape output -- use the CLI's own `--format` and `--filter` flags. You never redirect to files -- use the Write tool. You never chain -- run one command, confirm it succeeded, then run the next. When you reach for a pipe, you have not looked for the flag yet.

## Rule 1: No Pipes

The CLI already has the flag you are looking for. Pipes hide exit codes, split the atomic contract, and trigger extra permission prompts. For unbounded outputs, use native limit flags (`--limit=50`, `--freshness=1h`) -- never pipe to `head`.

## Rule 2: One Command Per Step

Chaining with `&&` or `;` breaks atomicity: you lose exit-code isolation and risk interactive prompts mid-chain blocking Claude Code.

## Rule 3: Use Claude Code Tools Over Bash

File I/O belongs to the tool layer, not the shell.

| Instead of | Use |
|------------|-----|
| `cat`, `head`, `tail` | Read tool |
| `echo >`, heredocs | Write tool |
| `sed -i`, `awk` | Edit tool |
| `grep -r`, `rg` | Grep tool |
| `find` | Glob tool |

**Never use heredocs** -- they fail in batch contexts. Exception: `git commit -m "$(cat <<'EOF' ...)"`.

## Rule 4: Absolute Paths

The working directory is not reliable across tool calls. Use absolute paths so each command is fully self-describing.

## Rule 7: Quote Variables

Always `"${VAR}"` to prevent word-splitting.

## Red Flags -- Stop Before Executing

If you are forming any of these thoughts, stop. You are about to violate the atomic contract:

- *"I'll use `|` to limit output"* -- Rule 1: use `--limit` or `--format`
- *"I'll pipe to `grep`/`awk`/`jq`"* -- Rule 1: use `--filter` and `--format`
- *"I'll chain with `&&`"* -- Rule 2: run separately, verify each exit code
- *"Let me save with `>`"* -- Rule 3: use the Write tool
- *"Let me `cat` this file"* -- Rule 3: use the Read tool
- *"Let me `cd` first"* -- Rule 4: use absolute path with `-chdir` or equivalent
- *"No spaces in this variable"* -- Rule 7: always quote

For mutation-specific rules (dry-run before apply, files over inline data), timeout tables, and cloud CLI examples, see `reference.md` in this skill directory.
