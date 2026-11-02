# Build

`build/gaia.manifest.json` declares what the single Gaia plugin ships and how its hooks are registered. Neither host reads it: Claude Code reads the files generated from it at the package root, `hooks/hooks.json` and `.claude-plugin/plugin.json`, and OpenCode reads the generated `opencode/agent-inventory.json`. Gaia ships one plugin, `gaia` (`scripts/build-plugin.py::VALID_PLUGINS`); there is no `dist/` bundle, because the npm package root is the plugin, and `.claude-plugin/marketplace.json` advertises it with `source: "."`, so `/plugin marketplace add metraton/gaia#<ref>` installs that ref at the version its `plugin.json` declares.

The manifest carries no permissions. The permission set merged into a workspace's `settings.local.json` is `hooks/modules/core/plugin_setup.py::OPS_PERMISSIONS`: an allow list and an empty ask list, with no deny rule. The `commands` array is empty; Gaia ships no slash-command files.

## How it is wired in

Consumed at pack time. `npm run generate:plugin-root` (also run by `prepack`) calls `python3 scripts/build-plugin.py gaia --manifests-only --output-dir .`, which regenerates three files in place:

```
build/gaia.manifest.json
   -> hooks/hooks.json               one entry per event in hooks.matchers, each running
                                     sh "${CLAUDE_PLUGIN_ROOT}/hooks/launch.sh" <entry point>;
                                     PreToolUse handlers carry onFailure: "block";
                                     host_mods become the top-level "modules" key
   -> .claude-plugin/plugin.json     name, version (from package.json), engines claude-code >=2.1.295; no hooks key
   -> opencode/agent-inventory.json  the agents list, for OpenCode
```

All three are committed. `scripts/check_hooks_drift.py` fails when a committed artifact differs from what the manifest produces, so an edit here is not finished until the generator has run and its output is committed with it.

What breaks if you get it wrong: the PreToolUse catch-all. Its matcher is a negative lookahead that sends every tool to the gate except a named list of built-ins that carry no command; it must equal `COMMAND_GATE_MATCHER` in `hooks/adapters/opencode_parity.py`, which a test compares. A tool added to that exclusion list stops being gated on Claude Code, and a tool missing from it spawns a hook process on every call.

## What's here

```
build/
├── README.md              # this file
└── gaia.manifest.json     # the single plugin's inventory and hook registration (source of the generated root files)
```

The manifest's keys:

- `hooks.entries` -- the hook entry points; `hooks.matchers` -- per event, the list of matchers that fire its entry point (`_get_entry_point` maps an event to its file).
- `host_mods` -- Claude Code mods the host loads in-process, relative to `hooks/` (`./mods/compaction/register.ts`).
- `modules` -- the Python hook-module directories; `"all"` resolves to `ALL_RESOLUTION["modules"]` in `scripts/build-plugin.py`. Not the same thing as `host_mods`.
- `agents` -- an explicit list of agent files, also the OpenCode inventory.
- `skills`, `tools`, `config` -- `"all"` (the whole directory) or a list of paths.
- `bin`, `include` -- the CLI and the runtime support it imports (`gaia/`, the database bootstrap scripts, `opencode/`).
- `version` -- always `"from:package.json"`.

## Conventions

- Update the manifest only when membership changes: a new hook entry point or matcher, an agent added or removed, a path outside a directory already covered. A new hook module, skill, CLI subcommand or migration is already covered by `modules: "all"`, `skills: "all"`, `bin/cli/`, `gaia/` or `scripts/migrations/`.
- `plugin_name` must equal the requested plugin name (`gaia`), or the build exits 1.
- Never edit `hooks/hooks.json`, `.claude-plugin/plugin.json` or `opencode/agent-inventory.json` by hand; edit the manifest and regenerate.

## See also

- [`scripts/build-plugin.py`](../scripts/build-plugin.py) -- the generator, and `ALL_RESOLUTION` for what `"all"` means.
- [`scripts/check_hooks_drift.py`](../scripts/check_hooks_drift.py) -- the guard that keeps the generated files equal to the manifest.
- [`hooks/README.md`](../hooks/README.md) -- what each entry point does once registered.
- [`agents/README.md`](../agents/README.md) -- the agent files the `agents` list names.
- [`package.json`](../package.json) -- the version source, `prepack`, and the `files` array that decides what is published.
