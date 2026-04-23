# Build

The `build/` directory contains the plugin manifests that tell Claude Code what Gaia ships. These are JSON files read once at startup — they register every hook entry point, every agent, every skill, and the settings permissions that the plugin contributes.

Gaia ships two plugins, not one. `gaia-ops` is the full system: orchestrator, specialist agents, skills, hooks, commands, and the complete permission set. `gaia-security` is the stripped-down path for teams that only want the security hooks — no agents, no skills, just the pre/post tool use pipeline that classifies and blocks commands. Each manifest is self-contained and activates an independent behavior profile.

When Claude Code loads a plugin, it reads the manifest to discover where the hooks live, which matchers should trigger them, and what permissions to merge into `settings.json` and `settings.local.json`. If a hook file listed in the manifest does not exist on disk, Claude Code silently skips it — there is no error, and that hook simply does not fire. This makes the manifest the authoritative list: if you add a new hook file but forget to register it here, it will never execute.

The `version` field in both manifests uses `"from:package.json"` — the build pipeline reads `package.json` and injects the actual version string before publishing. Never edit the version directly in the manifest files.

## Cuándo se activa

This component does not activate at runtime in the usual sense. The manifests are consumed once — at Claude Code plugin load time — and are not read again during the session.

```
npm install @jaguilar87/gaia
        |
Claude Code detects plugin in node_modules/
        |
Reads build/gaia-ops.manifest.json  (or gaia-security.manifest.json)
        |
Registers hooks: hooks/*.py -> matched to Claude Code events
        |
Registers agents: agents/*.md -> available for dispatch
        |
Registers skills: skills/*/ -> available for injection
        |
Merges permissions into settings.json / settings.local.json
        |
Session begins -- hooks fire based on registered matchers
```

If a hook file is listed in `entries` but does not exist on disk:
- Claude Code skips it silently
- That event type receives no Gaia processing
- Diagnosis: run `npx gaia-doctor` to detect missing hook files

## Qué hay aquí

```
build/
├── gaia-ops.manifest.json       # Full system: all hooks, agents, skills, commands, permissions
└── gaia-security.manifest.json  # Security-only: hooks + deny rules, no agents or skills
```

## Convenciones

**Hook entries:** List every hook file under `hooks.entries`. The order does not matter — Claude Code registers them by event type using the `matchers` object.

**Matchers:** The `matchers` object maps Claude Code event names (`PreToolUse`, `PostToolUse`, `SubagentStop`, etc.) to the tool names or patterns that trigger that hook. A matcher of `"*"` means fire for all tools of that event type.

**Agents field:** Array of paths relative to the package root. Each path must point to a `.md` file with valid YAML frontmatter.

**Skills/tools/config fields:** Accept `"all"` (include everything in that directory) or an array of specific paths.

**Version:** Always `"from:package.json"` — never a hardcoded string.

**Two-manifest rule:** Any hook that belongs in both plugins must be listed in both manifest files. Changes to shared hooks require updating both files.

## Ver también

- [`hooks/README.md`](../hooks/README.md) — hook entry points and pipeline architecture
- [`agents/README.md`](../agents/README.md) — agent definitions and frontmatter conventions
- [`bin/gaia-doctor.js`](../bin/gaia-doctor.js) — detects missing hooks and broken registrations
- [`package.json`](../package.json) — version source and `files` array (controls what gets published)
