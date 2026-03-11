---
name: scan-project
description: Scan the current project to detect stack, infrastructure, tools, and generate/update project-context.json
allowed-tools:
  - Bash(*)
  - Read
---

Run the gaia modular project scanner to detect the project stack, infrastructure,
git setup, CLI tools, orchestration, and runtime environment. The scanner produces
(or updates) `.claude/project-context/project-context.json` with structured,
machine-readable context that agents consume.

## What this does

The scanner runs 6 independent modules in parallel:
- **stack** -- languages, frameworks, package managers
- **git** -- platform, remotes, branching strategy, monorepo detection
- **infrastructure** -- cloud providers, IaC, CI/CD, containers
- **orchestration** -- Kubernetes, GitOps (Flux/Argo), Helm charts
- **tools** -- installed CLI tools (kubectl, terraform, gcloud, etc.)
- **environment** -- OS info, language runtimes, .env file patterns

It preserves agent-enriched sections (data added by agents via CONTEXT_UPDATE)
and merges new scan data with existing context using section-ownership rules.

## How to run

Run the scanner CLI:

```bash
python3 bin/gaia-scan.py
```

Optional flags:
- `--verbose` -- show scanner-by-scanner progress
- `--scanners stack,git` -- run only specific scanners
- `--output /path/to/output.json` -- custom output path
- `--check-staleness` -- skip scan if context is already fresh (<24h old)

$ARGUMENTS

## Expected output

The CLI writes project-context.json and prints a JSON summary to stdout:

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

Read the generated context to verify:

```bash
python3 -c "import json; d=json.load(open('.claude/project-context/project-context.json')); print(json.dumps(list(d.get('sections',{}).keys()), indent=2))"
```
