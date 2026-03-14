---
name: command-execution
description: Use when executing any bash command, CLI tool, or shell operation
metadata:
  user-invocable: false
  type: discipline
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

## Rule 5: Quote Variables

Always `"${VAR}"` to prevent word-splitting.

## Red Flags -- Stop Before Executing

If you are forming any of these thoughts, stop. You are about to violate the atomic contract:

- *"I'll use `|` to limit output"* -- Rule 1: use `--limit` or `--format`
- *"I'll pipe to `grep`/`awk`/`jq`"* -- Rule 1: use `--filter` and `--format`
- *"I'll chain with `&&`"* -- Rule 2: run separately, verify each exit code
- *"Let me save with `>`"* -- Rule 3: use the Write tool
- *"Let me `cat` this file"* -- Rule 3: use the Read tool
- *"Let me `cd` first"* -- Rule 4: use absolute path with `-chdir` or equivalent
- *"No spaces in this variable"* -- Rule 5: always quote
- *"I'll cd to the worktree and then run the command"* -- Rule 2: run `cd` as a separate Bash call, then run the command in the next call. Never chain with `&&`.

## Rationalizations

| Excuse | Reality |
|--------|---------|
| "I need to pipe for formatting" | Use `--format`, `--output`, or `-o` flags. The CLI already formats. |
| "I need to chain commands for efficiency" | Two fast commands with verified exit codes beat one fragile chain. |
| "This read-only command is safe to pipe" | Pipes still hide exit codes and trigger extra permission prompts. Safe does not mean atomic. |
| "I'll just use grep instead of the Grep tool" | Rule 3: use the Grep tool. Bash grep loses structured output and wastes a permission prompt. |
| "I need jq to parse JSON output" | Use `--format json` or `--output-format` at the source. If unavoidable, run jq as a separate command on a saved file. |
| "A heredoc is the cleanest way to pass multi-line input" | Rule 3: use the Write tool. Heredocs fail in batch contexts. |
| "I'll cd first, then run the command" | Rule 4: use absolute paths. Rule 2: never chain cd with &&. |

## Anti-Patterns

- Piping `kubectl get` to `grep` instead of using `-l` label selectors or `--field-selector`
- Chaining `cd dir && terraform plan` instead of `terraform -chdir=/absolute/path plan`
- Using `cat file | wc -l` instead of reading the file with the Read tool
- Running `echo "content" > file` instead of using the Write tool
- Using `find . -name "*.tf"` instead of the Glob tool

## Hook Enforcement

The `cloud_pipe_validator.py` hook module also enforces the no-pipes rule at runtime, rejecting commands that contain pipe operators.

For mutation-specific rules (dry-run before apply, files over inline data), timeout tables, and cloud CLI examples, see `reference.md` in this skill directory.
