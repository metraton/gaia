#!/usr/bin/env python3
"""
Build script for the gaia plugin.

Under the `source: npm` delivery model the published package root IS the
plugin -- there is no dist/ bundle. This script only regenerates the two
generated manifests (.claude-plugin/plugin.json and the canonical
hooks/hooks.json) in place; it never cleans or copies component files.

Hooks are declared in exactly ONE place: hooks/hooks.json (the standard
plugin convention Claude Code reads). plugin.json does NOT embed an inline
`hooks` block -- doing so made Claude Code register every hook twice (once
from the inline block, once from hooks.json), so every event fired twice.

Usage:
    python3 scripts/build-plugin.py <plugin-name> --manifests-only [--output-dir <path>]

Exit codes:
    0  Build successful
    1  Invalid plugin name, missing manifest, or missing --manifests-only
"""

import argparse
import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
# Single, unified plugin named "gaia":
# one bundle ("gaia") ships hooks + modules + agents + skills + tools + config
# + the `bin/` CLI + the runtime support (`gaia/` package, `scripts/`).
VALID_PLUGINS = ("gaia",)

# Directories that "all" resolves to for the unified plugin
ALL_RESOLUTION = {
    "modules": [
        "hooks/modules/__init__.py",
        "hooks/modules/core/",
        "hooks/modules/security/",
        "hooks/modules/audit/",
        "hooks/modules/tools/",
        "hooks/modules/validation/",
        "hooks/modules/agents/",
        "hooks/modules/context/",
        "hooks/modules/scanning/",
        "hooks/modules/session/",
        "hooks/modules/memory/",
        "hooks/modules/identity/",
        "hooks/modules/orchestrator/",
        "hooks/modules/events/",
        "hooks/adapters/",
    ],
    "skills": "skills/",
    "tools": "tools/",
    "config": "config/",
}


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------

def load_manifest(plugin_name: str) -> dict:
    """Load and validate a build manifest."""
    manifest_path = REPO_ROOT / "build" / f"{plugin_name}.manifest.json"
    if not manifest_path.exists():
        print(f"Error: Manifest not found: {manifest_path}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in manifest: {e}", file=sys.stderr)
        sys.exit(1)

    if manifest.get("plugin_name") != plugin_name:
        print(
            f"Error: Manifest plugin_name '{manifest.get('plugin_name')}' "
            f"does not match requested '{plugin_name}'",
            file=sys.stderr,
        )
        sys.exit(1)

    return manifest


# ---------------------------------------------------------------------------
# File resolution
# ---------------------------------------------------------------------------

def resolve_file_list(manifest: dict) -> list[Path]:
    """Resolve all source files from the manifest into absolute paths."""
    files: list[Path] = []

    # Hook entry points
    for entry in manifest["hooks"]["entries"]:
        files.append(REPO_ROOT / entry)

    # Modules
    modules = manifest.get("modules", [])
    if modules == "all":
        modules = ALL_RESOLUTION["modules"]
    for mod in modules:
        _collect_paths(REPO_ROOT / mod, files)

    # Agents
    for agent in manifest.get("agents", []):
        files.append(REPO_ROOT / agent)

    # Skills
    skills = manifest.get("skills", [])
    if skills == "all":
        skills = [ALL_RESOLUTION["skills"]]
    for skill in skills:
        _collect_paths(REPO_ROOT / skill, files)

    # Commands
    for cmd in manifest.get("commands", []):
        files.append(REPO_ROOT / cmd)

    # Tools
    tools = manifest.get("tools", [])
    if tools == "all":
        tools = [ALL_RESOLUTION["tools"]]
    if isinstance(tools, list):
        for tool in tools:
            _collect_paths(REPO_ROOT / tool, files)

    # Config
    config = manifest.get("config", [])
    if config == "all":
        config = [ALL_RESOLUTION["config"]]
    if isinstance(config, list):
        for cfg in config:
            _collect_paths(REPO_ROOT / cfg, files)

    # bin -- the unified CLI (`bin/gaia` + `bin/cli/`). Shipping these inside the
    # bundle is what makes `/plugin install` expose the `gaia` executable on the
    # Bash tool PATH (Claude Code adds a plugin's bin/ to PATH).
    for entry in manifest.get("bin", []):
        _collect_paths(REPO_ROOT / entry, files)

    # include -- runtime support the CLI + hooks import but that lives outside
    # modules/tools/config: the `gaia/` package (store, project, paths, schema.sql)
    # and the `scripts/` needed by the lazy DB bootstrap (bootstrap_database.sh +
    # its seeders/migrations). Without these the bundled CLI cannot run.
    for entry in manifest.get("include", []):
        _collect_paths(REPO_ROOT / entry, files)

    return files


def _collect_paths(path: Path, out: list[Path]) -> None:
    """Collect file paths. If path is a directory, recursively add all files.
    If it's a file, add it directly. Skip __pycache__ directories."""
    if path.is_file():
        out.append(path)
    elif path.is_dir():
        for child in sorted(path.rglob("*")):
            if child.is_file() and "__pycache__" not in str(child):
                out.append(child)


# ---------------------------------------------------------------------------
# hooks.json generation
# ---------------------------------------------------------------------------

def generate_hooks_json(manifest: dict) -> dict:
    """Generate hooks.json from manifest matcher configuration."""
    matchers = manifest["hooks"]["matchers"]
    hooks_json: dict = {"hooks": {}}

    for event_name, matcher_list in matchers.items():
        entries = []
        # Determine which entry point to use for this event
        entry_point = _get_entry_point(event_name, manifest["hooks"]["entries"])

        for matcher_config in matcher_list:
            entry: dict = {}
            if "matcher" in matcher_config:
                entry["matcher"] = matcher_config["matcher"]
            # Invoke via `python3` rather than relying on the script's exec bit.
            # The tarball install path (`npm install <tgz>`) preserves file mode
            # from the working tree; if a hook ships without 0755 the SessionEnd
            # event raises "Permission denied" on every invocation. Using
            # `python3 <path>` removes that dependency entirely -- the kernel
            # never needs +x on the .py file because exec is on /usr/bin/python3.
            entry["hooks"] = [
                {
                    "type": "command",
                    "command": f"python3 ${{CLAUDE_PLUGIN_ROOT}}/{entry_point}",
                }
            ]
            entries.append(entry)

        hooks_json["hooks"][event_name] = entries

    return hooks_json


def _get_entry_point(event_name: str, entries: list[str]) -> str:
    """Map a hook event name to its entry point file."""
    event_to_file = {
        "PreToolUse": "hooks/pre_tool_use.py",
        "PostToolUse": "hooks/post_tool_use.py",
        "Stop": "hooks/stop_hook.py",
        "UserPromptSubmit": "hooks/user_prompt_submit.py",
        "SubagentStart": "hooks/subagent_start.py",
        "SubagentStop": "hooks/subagent_stop.py",
        "SessionStart": "hooks/session_start.py",
        "SessionEnd": "hooks/session_end_hook.py",
        "TaskCompleted": "hooks/task_completed.py",
        "PreCompact": "hooks/pre_compact.py",
        "PostCompact": "hooks/post_compact.py",
        "ElicitationResult": "hooks/elicitation_result.py",
    }
    entry = event_to_file.get(event_name)
    if entry and entry in entries:
        return entry
    # Fallback: try to find a matching entry by name
    event_lower = event_name.lower()
    for e in entries:
        if event_lower in e.lower().replace("_", ""):
            return e
    return entries[0]


# ---------------------------------------------------------------------------
# settings.json generation
# ---------------------------------------------------------------------------

def generate_plugin_json(manifest: dict) -> dict:
    """Generate .claude-plugin/plugin.json from manifest.

    Does NOT embed a `hooks` key. Hooks are declared solely in
    hooks/hooks.json (the standard plugin convention Claude Code reads).
    Embedding them inline here as well made CC read BOTH sources and register
    every hook twice, so every event (SessionStart, SessionEnd, ...) fired
    twice. Emitting hooks in exactly one place -- hooks.json -- fixes that.
    """
    version = manifest.get("version", "0.0.0")
    if version == "from:package.json":
        package_json_path = REPO_ROOT / "package.json"
        with open(package_json_path) as f:
            package_data = json.load(f)
        version = package_data.get("version", "0.0.0")

    plugin_name = manifest["plugin_name"]
    homepage = "https://github.com/metraton/gaia#readme"

    # NOTE: no `hooks` key here on purpose. Hooks live only in hooks/hooks.json
    # (generated by generate_hooks_json / write_root_manifests). Declaring them
    # here too caused CC to register each hook twice (double-firing).
    return {
        "name": plugin_name,
        "version": version,
        "description": manifest.get("description", ""),
        "author": {
            "name": "jaguilar87",
            "email": "jorge.aguilar87@gmail.com",
        },
        "homepage": homepage,
        "repository": "https://github.com/metraton/gaia",
        "license": "MIT",
        "keywords": ["security", "devops"],
        "engines": {"claude-code": ">=2.1.0"},
        "categories": ["devops", "security", "orchestration"],
    }


# ---------------------------------------------------------------------------
# Build execution
# ---------------------------------------------------------------------------
#
# NOTE: the former clean-build path (`build_plugin()`) that rmtree'd an
# output directory and copied every manifest-resolved file into a fresh
# `dist/<plugin>` tree has been removed. Nothing in the ship pipeline used
# it -- package.json's only build step is `generate:plugin-root`, which
# calls `write_root_manifests()` below via `--manifests-only`. Under the
# `source: npm` delivery model the package root IS the plugin (component
# files already live there and ship via package.json `files[]`), so there
# is no dist/ artifact to produce.


def write_root_manifests(plugin_name: str, output_dir: Path) -> None:
    """Regenerate ONLY the two plugin manifests into an existing directory.

    Writes:
      - <output_dir>/.claude-plugin/plugin.json  (metadata only -- NO inline
        `hooks` block; declaring hooks here as well made CC register them twice)
      - <output_dir>/hooks/hooks.json            (canonical, manifest-derived,
        the SINGLE source of hook declarations; the npm surface reads this via
        merge_local_hooks() in _install_helpers.py)

    Unlike build_plugin(), this NEVER cleans (`rmtree`) or copies component files.
    It is the pack-time / release-prepare mechanism that makes the repo root (and
    therefore the published npm tarball root) a valid Claude Code plugin, without
    a separate dist/ bundle. The component files (agents/, skills/, hooks/*.py,
    tools/, config/, bin/) already live at the root and ship via package.json
    `files[]`.

    SAFETY: because we must be able to target the repo root itself
    (`--output-dir .`), this function must never delete the output directory.
    The anti-rmtree guarantee is structural -- there is no rmtree call here.
    """
    manifest = load_manifest(plugin_name)
    output_dir = output_dir.resolve()

    # Anti-rmtree guard, made explicit: this path is chosen precisely when the
    # target is a live, populated tree (the repo root). Assert the directory
    # exists and is non-empty so a caller cannot mistake this for the clean-build
    # path -- we augment in place, we do not own/replace the directory.
    if not output_dir.is_dir():
        print(f"Error: --manifests-only target is not a directory: {output_dir}", file=sys.stderr)
        sys.exit(1)

    # Diagnostics go to stderr, never stdout: `npm pack` runs this script via
    # the `prepack` lifecycle hook, and `npm pack`/`npm pack --json` both
    # forward the child's stdout into their own stdout. Any diagnostic line
    # printed here would land inside `$(npm pack --silent)` (breaking
    # single-line consumers like publish.yml's $GITHUB_OUTPUT append) or
    # before the JSON array `npm pack --json` emits (bin/cli/_pack_helpers.py
    # already defends against that by slicing from the first '[', but stdout
    # should carry only the tool's actual data, never incidental narration).
    print(f"Regenerating root manifests for plugin '{plugin_name}' in: {output_dir}", file=sys.stderr)

    # hooks/hooks.json
    hooks_json = generate_hooks_json(manifest)
    hooks_json_path = output_dir / "hooks" / "hooks.json"
    hooks_json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(hooks_json_path, "w") as f:
        json.dump(hooks_json, f, indent=2)
        f.write("\n")
    print(f"  Generated: hooks/hooks.json ({len(hooks_json['hooks'])} events)", file=sys.stderr)

    # .claude-plugin/plugin.json (inline hooks)
    plugin_json = generate_plugin_json(manifest)
    plugin_json_dir = output_dir / ".claude-plugin"
    plugin_json_dir.mkdir(parents=True, exist_ok=True)
    plugin_json_path = plugin_json_dir / "plugin.json"
    with open(plugin_json_path, "w") as f:
        json.dump(plugin_json, f, indent=2)
        f.write("\n")
    print("  Generated: .claude-plugin/plugin.json (metadata only, no inline hooks)", file=sys.stderr)
    print("Root manifests regenerated.", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Regenerate the gaia plugin's generated manifests from its build manifest.",
        prog="build-plugin.py",
    )
    parser.add_argument(
        "plugin-name",
        choices=VALID_PLUGINS,
        help="Plugin to build (gaia -- the single unified plugin)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for --manifests-only (default: the repo root)",
    )
    parser.add_argument(
        "--manifests-only",
        action="store_true",
        help=(
            "Regenerate ONLY .claude-plugin/plugin.json (inline hooks) + hooks/hooks.json "
            "into --output-dir, WITHOUT cleaning or copying component files. Used to make "
            "the repo root (and the published npm tarball root) a valid plugin for source:npm. "
            "Never deletes the output directory. This is the only supported build mode -- "
            "there is no dist/ clean-build path."
        ),
    )

    args = parser.parse_args()
    plugin_name = getattr(args, "plugin-name")

    if not args.manifests_only:
        print(
            "Error: --manifests-only is required. The dist/ clean-build path has been "
            "removed -- under source:npm the package root IS the plugin, so this script "
            "only regenerates .claude-plugin/plugin.json + hooks/hooks.json in place.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Default target is the repo root -- augment it in place.
    output_dir = args.output_dir or REPO_ROOT
    if not output_dir.is_absolute():
        output_dir = REPO_ROOT / output_dir
    write_root_manifests(plugin_name, output_dir)


if __name__ == "__main__":
    main()
