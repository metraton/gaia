---
name: command-execution
description: Use when executing any bash command, CLI tool, or shell operation
---

# Command Execution

```
ONE COMMAND. ONE RESULT. ONE EXIT CODE.
Reach for the native flag before the pipe; the file tool before the shell.
```

The runtime hard-blocks pipes, redirects, and chaining for cloud CLIs (gcloud kubectl aws terraform helm flux) and blocks redirects and background `&` for every command — but the discipline applies to everything you run, not only what the hook catches.

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

The agent cwd resets between Bash calls, so a relative path resolves against an unknown directory — pass absolute paths or the CLI's `-chdir`.

## Rules

1. **No pipes** — find the CLI's native flag first.
2. **One command per step** — no `&&` or `;`.
2b. **No indirect-execution wrappers** — no `bash -c`, `sh -c`, `eval`, or similar. They hide the real command inside a string, which the classifier cannot see and which trips the runtime's indirect-execution guardrail (an "ask" dialog even for commands that would otherwise pass). Run the discrete command directly, or put multi-step logic in a committed script file and invoke it directly (`python3 script.py`, `./script.sh`) instead of wrapping it in an interpreter's `-c`/`-e` flag.
2c. **No fabricated `.claude/` fixtures** — never hand-roll `.claude/` directories or files to simulate install/sandbox behavior (e.g. `mkdir fake-sandbox/.claude`). `.claude/` paths are protected by a native sensitive-path guard, so a fabricated fixture both trips that guard and is not a faithful reproduction. To exercise install/sandbox behavior, run the real tooling (`bin/validate-sandbox.sh`) under proper isolation — an ephemeral temp workspace with `GAIA_DATA_DIR=<tmp>` — never a substitute you construct by hand. If the real tooling is T3-gated, request approval (`APPROVAL_REQUEST`) rather than dodging it with a fabricated fixture.
3. **Tools over Bash** — for file I/O, always.
4. **Absolute paths** — agent cwd resets between calls; relative paths break silently.
5. **Quote variables** — unquoted `${VAR}` with spaces becomes multiple arguments.
6. **No redirects or background** — `>`/`>>` and trailing `&` are the part the runtime enforces on every command; redirects bypass the Write tool, `&` hides the exit code.

## Traps

| If you're thinking... | The reality is... |
|---|---|
| "I'll pipe to filter / parse / it's read-only so it's safe" | The flag exists: `--filter`, `--format`, `-o jsonpath`. A pipe hides the exit code regardless of intent |
| "I'll chain with && for efficiency" | Chaining collapses two exit codes into one — run separately and verify each |
| "I'll wrap this in bash -c / eval to run it in one call" | Wrapping hides the real command from the classifier and trips the indirect-execution guardrail — run it as a discrete command or a script file instead |
| "I'll just mkdir a fake `.claude/` to simulate the sandbox" | `.claude/` is a protected path — a hand-rolled fixture trips the sensitive-path guard and isn't a faithful repro; run `bin/validate-sandbox.sh` under isolation (temp workspace + `GAIA_DATA_DIR`) instead |
| "Let me cat/head this file (or use a heredoc)" | File I/O is a tool, not a shell call — use Read/Write; heredocs also break in batch |
| "Let me cd first, then run" | The cwd resets between calls — use an absolute path or `-chdir` |
| "Redirect output to a file / run it in background" | Redirect and `&` are the universal wall — use the Write tool; `&` hides the exit code, blocked for every command |

## Anti-Patterns

- `kubectl get pods | grep Error` → use `-l` label selectors or `--field-selector`
- `cd dir && terraform plan` → `terraform -chdir=/absolute/path plan`
- `cat file | wc -l` → Read tool
- `bash -c "mv a b"` → run `mv a b` directly, or put it in a script file and invoke that
- `mkdir fake-sandbox/.claude` to simulate `validate-sandbox.sh` → run the real script under a temp workspace with `GAIA_DATA_DIR` set

Enforced at runtime by `validate_cloud_pipe` (`cloud_pipe_validator.py`): pipes/redirects/chaining are blocked for cloud CLIs; redirects and background `&` are blocked for every command. A quoted `git commit -m "$(cat <<'EOF' …)"` passes because the body is quote-stripped before scanning and `git` is non-cloud — not a special case. See `reference.md` for mutation rules and cloud examples.
