---
name: scan-project
description: Scan the current project to detect stack, infrastructure, tools, and update the project context in ~/.gaia/gaia.db
allowed-tools:
  - Bash(*)
  - Read
---

Run the gaia modular project scanner to detect the project stack, infrastructure,
git setup, CLI tools, orchestration, and runtime environment. The scanner writes
structured, machine-readable context to `~/.gaia/gaia.db` that agents consume.

No `project-context.json` file is generated. The DB is the canonical source of
truth. Use `gaia context show` to inspect the stored context.

## What this does

The scanner runs 6 independent modules in parallel:
- **stack** -- languages, frameworks, package managers
- **git** -- platform, remotes, branching strategy, monorepo detection
- **infrastructure** -- cloud providers, IaC, CI/CD, containers
- **orchestration** -- Kubernetes, GitOps (Flux/Argo), Helm charts
- **tools** -- installed CLI tools (kubectl, terraform, gcloud, etc.)
- **environment** -- OS info, language runtimes, .env file patterns

It preserves agent-enriched sections (data added by agents via update_contracts)
and merges new scan data with existing context using section-ownership rules.
Projects that temporarily disappear are soft-deleted (`status='missing'`) and
reactivated when they reappear -- data is never purged.

## How to run

```bash
gaia scan
```

Optional flags:
- `--verbose` -- show scanner-by-scanner progress
- `--scanners stack,git` -- run only specific scanners
- `--check-staleness` -- skip scan if context is already fresh (<24h old)

$ARGUMENTS

## Expected output

The CLI prints a JSON summary to stdout:

```
{
  "status": "success",
  "scanner_version": "0.1.0",
  "sections_updated": ["project_identity", "stack", "git", ...],
  "scanners_run": 6,
  "warnings_count": 0,
  "duration_ms": 2500.0
}
```

A human-readable summary is also printed to stderr showing scanner count,
section count, warnings, and elapsed time.

## After scanning

Inspect the stored context:

```bash
gaia context show
```

Or query a specific section:

```bash
gaia context get stack
```
