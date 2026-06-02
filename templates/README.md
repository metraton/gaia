# Templates

Templates are the reference files that Gaia uses to generate per-project configuration. They are not consumed by the Claude Code runtime — they are consumed by the install scripts in `bin/` and by organization administrators deploying managed policies. Think of this directory as the catalog of files that exist as skeletons, ready to be filled in during installation or deployed verbatim as a policy.

There are two audiences for this directory, and they do not overlap. The first is the individual developer installing Gaia into their project — `gaia install` (run by the npm postinstall hook) bootstraps the DB and `.claude/` structure. The second is the enterprise administrator — they take `managed-settings.template.json` and deploy it as a managed policy via the Claude.ai Admin Console or by placing it at `/etc/claude-code/managed-settings.json` on managed workstations. `gaia scan` (separate from install) writes project context to `~/.gaia/gaia.db` only; it does not read or interpolate templates.

Keeping these files here, rather than embedding them in `bin/cli/scan.py`, means policies and skeletons can be audited and customized without touching executable code. An admin can diff `managed-settings.template.json` against a previous version. A developer can read `governance.template.md` (when present) before letting the scanner interpolate it.

## Cuándo se activa

This component does not activate in the runtime Claude Code pipeline. Templates are consumed only by install-time tooling and by administrators deploying policies out-of-band.

**When each template is consumed:**

```
Individual developer runs: npm install @jaguilar87/gaia
        |
postinstall -> gaia install --postinstall
        |
Install bootstraps ~/.gaia/gaia.db, creates .claude/ symlinks, merges settings.
Templates in this directory are NOT read during install or scan.

Developer separately runs: gaia scan
        |
bin/cli/scan.py detects project stack and writes to ~/.gaia/gaia.db.
gaia scan does NOT read templates/ or generate any files.
```

```
Enterprise admin deploys managed policy
        |
Admin copies templates/managed-settings.template.json
        |
Deploys to Claude.ai Admin Console
   OR writes to /etc/claude-code/managed-settings.json (Linux managed workstations)
        |
Managed settings take highest precedence — cannot be overridden by user or project
```

## Qué hay aquí

```
templates/
├── managed-settings.template.json   # Enterprise reference — deployed by admin, not gaia-scan
└── README.md
```

Currently only `managed-settings.template.json` ships in this directory. A `governance.template.md` has been referenced in prior docs but is not present in source and is not consumed by any current automated step (`gaia scan` writes to DB only; `gaia install` does not interpolate templates). If a future installer step consumes templates, this note should be updated.

## Convenciones

**Audience per file:**

| File | Audience | Consumed by | Trigger |
|------|----------|-------------|---------|
| `managed-settings.template.json` | Enterprise administrator | Claude.ai Admin Console or `/etc/claude-code/managed-settings.json` | Admin action — out of band, not automated |
| `governance.template.md` (if present) | Individual developer | Future install tooling (not yet implemented) | Not currently consumed by any automated step |

**Managed settings precedence:** `managed-settings.template.json` contains wildcard deny rules that cannot be overridden by user or project settings. It also sets `disableBypassPermissionsMode: true` to prevent `--dangerously-skip-permissions`. Deploy this only when you want organization-wide enforcement.

**No CLAUDE.md generated:** Orchestrator identity is no longer generated from a template. It lives in `agents/gaia-orchestrator.md` and is activated via `settings.json: { "agent": "gaia-orchestrator" }`. Surface routing is injected by the `UserPromptSubmit` hook, not by a template.

**Template naming:** Files intended for interpolation use the `.template.<ext>` suffix (e.g., `governance.template.md`, `managed-settings.template.json`). Files without that suffix should not be here.

## Ver también

- [`bin/cli/install.py`](../bin/cli/install.py) — `gaia install` (postinstall) bootstraps the DB and `.claude/` structure; templates are not currently read by any automated step
- [`bin/cli/update.py`](../bin/cli/update.py) — `gaia update` updates settings.local.json (merges, does not use templates here)
- [`agents/gaia-orchestrator.md`](../agents/gaia-orchestrator.md) — orchestrator identity (replaces old CLAUDE.md template path)
- [`build/gaia-ops.manifest.json`](../build/gaia-ops.manifest.json) — plugin-level permission defaults (distinct from managed-settings)
