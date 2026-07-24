# Build

The `build/` directory contains the plugin manifest that tells Claude Code what Gaia ships. It is a JSON file read once at startup — it registers every hook entry point, every agent, and every skill. The manifest does **not** carry a permissions block; the workspace permission set is owned by `hooks/modules/core/plugin_setup.py` (`OPS_PERMISSIONS`), the single source of truth for what gets merged into `settings.local.json`.

Gaia ships as a **single, unified plugin** named `gaia`. The canonical manifest is `gaia.manifest.json` — one bundle carrying the full system: orchestrator, specialist agents, skills, hooks, tools, config, and a `bin` list. The `commands` array is present but currently **empty** (`"commands": []`) — Gaia ships no standalone slash-command files; slash commands are surfaced from skills. (`scripts/build-plugin.py` has `VALID_PLUGINS = ("gaia",)`, and `.claude-plugin/marketplace.json` advertises one plugin whose `source` is `{"source": "github", "repo": "metraton/gaia"}` -- there is no `dist/` bundle; the npm package root IS the plugin.)

When Claude Code loads the plugin, it reads the manifest to discover where the hooks live and which matchers should trigger them. (The permission set merged into `settings.local.json` is not read from the manifest — it is defined in `hooks/modules/core/plugin_setup.py::OPS_PERMISSIONS`.) If a hook file listed in the manifest does not exist on disk, Claude Code silently skips it — there is no error, and that hook simply does not fire. This makes the manifest the authoritative list: if you add a new hook file but forget to register it here, it will never execute.

The `version` field uses `"from:package.json"` — the build pipeline reads `package.json` and injects the actual version string before publishing. Never edit the version directly in the manifest.

## Cuándo se activa

This component does not activate at runtime in the usual sense. The manifest is consumed twice — once by `scripts/build-plugin.py` at pack time (regenerating the package root in place, no `dist/` bundle), and once by Claude Code at plugin load time — and is not read again during the session.

```
npm run generate:plugin-root  ->  python3 scripts/build-plugin.py gaia --manifests-only --output-dir .
        |
Reads build/gaia.manifest.json
        |
Resolves "all" fields to concrete file lists
        |
Regenerates hooks/hooks.json (the hooks CC loads) + .claude-plugin/plugin.json (metadata only, no inline hooks) in place at the package root
        |
--- later, when Claude Code loads the plugin ---
        |
Registers hooks: hooks/*.py -> matched to Claude Code events
        |
Registers agents: agents/*.md -> available for dispatch
        |
Registers skills: skills/*/ -> available for injection
        |
Merges permissions into settings.local.json (npm surface; source is plugin_setup.py::OPS_PERMISSIONS, not the manifest)
        |
Session begins -- hooks fire based on registered matchers
```

If a hook file is listed in `entries` but does not exist on disk:
- Claude Code skips it silently
- That event type receives no Gaia processing
- Diagnosis: run `gaia doctor` to detect missing hook files

## Qué hay aquí

```
build/
├── README.md                    # this file
└── gaia.manifest.json           # CANONICAL: the single unified plugin (all hooks, agents, skills, tools, config, permissions)
```

## Convenciones

**Hook entries:** List every hook file under `hooks.entries`. The order does not matter — Claude Code registers them by event type using the `matchers` object.

**Matchers:** The `matchers` object maps Claude Code event names (`PreToolUse`, `PostToolUse`, `SubagentStop`, etc.) to the tool names or patterns that trigger that hook. A matcher of `"*"` means fire for all tools of that event type.

**Agents field:** Array of paths relative to the package root. Each path must point to a `.md` file with valid YAML frontmatter.

**Skills/tools/config fields:** Accept `"all"` (include everything in that directory) or an array of specific paths.

**Version:** Always `"from:package.json"` — never a hardcoded string.

**Manifest–plugin-name rule:** `scripts/build-plugin.py` requires the manifest's `plugin_name` field to equal the requested plugin name (`gaia`). Editing one without the other fails the build.

## Ver también

- [`hooks/README.md`](../hooks/README.md) — hook entry points and pipeline architecture
- [`agents/README.md`](../agents/README.md) — agent definitions and frontmatter conventions
- [`scripts/build-plugin.py`](../scripts/build-plugin.py) — reads this manifest and regenerates the package root's `.claude-plugin/plugin.json` + `hooks/hooks.json` in place
- [`bin/cli/doctor.py`](../bin/cli/doctor.py) — `gaia doctor` detects missing hooks and broken registrations
- [`package.json`](../package.json) — version source and `files` array (controls what gets published)
