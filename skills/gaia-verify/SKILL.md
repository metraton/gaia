---
name: gaia-verify
description: Use when the user wants to verify a Gaia installation -- "probemos", "verify", "test installation", "gaia-verify"
metadata:
  user-invocable: true
  type: technique
---

# Gaia Verify

Verify that a Gaia installation works correctly across 4 modes. Each mode tests a different delivery surface. Use the mode that matches what was just changed or installed.

## Decision Tree

```
"probemos" / "verify" / "test installation"
├─ Just edited source code?       -> live
├─ About to publish to npm?       -> dry-run
├─ Just published @beta?          -> beta
└─ Just published @latest?        -> release
```

If the user does not specify a mode, ask: "Which mode -- live, dry-run, beta, or release?"

## Mode: live

Tests the current symlinked installation. Source code is live -- no build step.

**When:** After editing source files in `gaia-ops-dev/`

Commands: run `gaia-doctor` then `gaia-status` directly (already installed, no npx needed).

**No temp directory.** No cleanup needed.

## Mode: dry-run

Tests the build pipeline -- does `npm pack` + local install produce a working installation?

**When:** Before publishing to npm

Step-by-step commands in `reference.md`. Core flow: `npm pack` in `gaia-ops-dev` -> install `.tgz` in `/tmp/gaia-dry-run-{timestamp}` -> `npx gaia-doctor` + `npx gaia-status` -> clean up.

## Mode: beta

Tests the published `@beta` tag on the npm registry.

**When:** After publishing a beta release via the pipeline

Step-by-step commands in `reference.md`. Core flow: fresh `/tmp/gaia-beta-verify-{timestamp}` -> `npm install @jaguilar87/gaia-ops@beta` -> `npx gaia-doctor` + `npx gaia-status` -> clean up.

## Mode: release

Tests the published `@latest` tag on the npm registry.

**When:** After publishing a stable release via the pipeline

Step-by-step commands in `reference.md`. Core flow: fresh `/tmp/gaia-release-verify-{timestamp}` -> `npm install @jaguilar87/gaia-ops@latest` -> `npx gaia-doctor` + `npx gaia-status` -> clean up.

## All Modes: Reporting

Every mode ends with a structured result:

```
Mode:     <live | dry-run | beta | release>
Version:  <version string installed, or "symlinked source" for live>
Doctor:   PASS | FAIL
Status:   <gaia-status output summary>
Cleanup:  done | n/a (live)
```

If `gaia-doctor` fails, report the exact error and stop -- do not continue to `gaia-status`.

## Anti-Patterns

- **Skipping the mode question** -- each mode tests a different surface; running the wrong one gives false confidence.
- **Skipping cleanup** -- `/tmp/gaia-{mode}-*` directories accumulate; always delete after reporting.
- **Continuing after doctor failure** -- a failing doctor means the installation is broken; status output is meaningless.
