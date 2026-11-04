#!/usr/bin/env python3
"""
Build script for the gaia plugin.

Under the `source: npm` delivery model the published package root IS the
plugin -- there is no dist/ bundle. This script regenerates the generated
plugin metadata, hook configuration, and OpenCode agent inventory in place;
it never cleans or copies component files.

Hooks are declared in exactly ONE place: hooks/hooks.json. plugin.json embeds
no `hooks` block, because Claude Code would register every hook twice.

Usage:
    python3 scripts/build-plugin.py <plugin-name> --manifests-only [--output-dir <path>] [--force]

A regeneration that would CHANGE an existing generated manifest (hooks.json,
plugin.json, agent-inventory.json) refuses without --force, wherever the output
directory sits: npm prepack runs this script as a subprocess side effect, and a
silent rewrite there would apply a change no one consented to. An in-sync tree
regenerates as a no-op.

Exit codes:
    0  Build successful
    1  Invalid plugin name, missing manifest, or missing --manifests-only
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
# Single, unified plugin named "gaia":
# one bundle ("gaia") ships hooks + modules + agents + skills + tools + config
# + the `bin/` CLI + the runtime support (`gaia/` package, `scripts/`).
VALID_PLUGINS = ("gaia",)

# Every registered hook command runs its entrypoint through this script.
HOOK_LAUNCHER = "hooks/launch.sh"

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

    for mod in manifest.get("host_mods", []):
        _collect_paths((REPO_ROOT / "hooks" / mod).parent, files)

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
        entry_point = _get_entry_point(event_name, manifest["hooks"]["entries"])

        for matcher_config in matcher_list:
            entry: dict = {}
            if "matcher" in matcher_config:
                entry["matcher"] = matcher_config["matcher"]
            # `sh <launcher>` needs no exec bit (a tarball install keeps the
            # working tree's file modes) and no `python3` (the launcher picks
            # python3, python or `py -3`); both paths are quoted because a
            # plugin root or workspace can hold a space.
            handler: dict = {
                "type": "command",
                "command": (
                    f'sh "${{CLAUDE_PLUGIN_ROOT}}/{HOOK_LAUNCHER}" '
                    f'"${{CLAUDE_PLUGIN_ROOT}}/{entry_point}"'
                ),
            }
            # The host runs the tool when a hook cannot start or exits other
            # than 0/2 (missing sh or Python, crash before a verdict); "block"
            # makes those failures deny. Only the gate gets it: on Stop or
            # SessionStart a blocking failure would trap the session.
            if event_name == "PreToolUse":
                handler["onFailure"] = "block"
            entry["hooks"] = [handler]
            entries.append(entry)

        hooks_json["hooks"][event_name] = entries

    # Claude Code mods: hooks modules the host loads in-process, relative to
    # hooks/hooks.json. Not the manifest's `modules` key, which lists Python
    # hook-module directories.
    mods = manifest.get("host_mods", [])
    if mods:
        hooks_json["modules"] = list(mods)

    return hooks_json


def _get_entry_point(event_name: str, entries: list[str]) -> str:
    """Map a hook event name to its entry point file."""
    event_to_file = {
        "PreToolUse": "hooks/pre_tool_use.py",
        "PostToolUse": "hooks/post_tool_use.py",
        "PostToolUseFailure": "hooks/post_tool_use.py",
        "Stop": "hooks/stop_hook.py",
        "UserPromptSubmit": "hooks/user_prompt_submit.py",
        "SubagentStart": "hooks/subagent_start.py",
        "SubagentStop": "hooks/subagent_stop.py",
        "SessionStart": "hooks/session_start.py",
        "SessionEnd": "hooks/session_end_hook.py",
        "TaskCompleted": "hooks/task_completed.py",
        "PreCompact": "hooks/pre_compact.py",
        "PostCompact": "hooks/post_compact.py",
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

    Carries no `hooks` key: hooks are declared solely in hooks/hooks.json, and
    declaring them here too would register each hook twice.
    """
    version = manifest.get("version", "0.0.0")
    if version == "from:package.json":
        package_json_path = REPO_ROOT / "package.json"
        with open(package_json_path) as f:
            package_data = json.load(f)
        version = package_data.get("version", "0.0.0")

    plugin_name = manifest["plugin_name"]
    homepage = "https://github.com/metraton/gaia#readme"

    return {
        "name": plugin_name,
        "version": version,
        "description": manifest.get("description", ""),
        "author": {
            "name": "jaguilar87",
        },
        "homepage": homepage,
        "repository": "https://github.com/metraton/gaia",
        "license": "MIT",
        "keywords": ["security", "devops"],
        "engines": {"claude-code": ">=2.1.295"},
        "categories": ["devops", "security", "orchestration"],
    }


def generate_opencode_agent_inventory(manifest: dict) -> dict:
    """Generate the packaged OpenCode inventory from the agent manifest."""
    agents = manifest.get("agents")
    if not isinstance(agents, list) or not all(isinstance(item, str) for item in agents):
        raise ValueError("build manifest must declare a string agents inventory")
    return {"version": 1, "agents": agents}


# ---------------------------------------------------------------------------
# Build execution
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write ``data`` to ``path`` as JSON with no truncated/empty-file window.

    ``open(path, "w")`` truncates before the new content is written, so any
    concurrent reader (an ``npm pack`` racing a second ``npm pack``, a
    packaging tool, a test) can observe a zero-byte or partial file between
    the truncate and the write completing. Writing to a temp file in the SAME
    directory and ``os.replace()``-ing it over the target closes that window
    for every reader: ``os.replace`` is atomic on POSIX and Windows, so a
    concurrent open of ``path`` always sees either the old complete content or
    the new complete content, never neither. Same-directory placement matters
    -- ``os.replace`` across filesystems is not guaranteed atomic.
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _write_generated_manifest(path: Path, data: dict, *, force: bool) -> str:
    """Write one generated manifest, refusing a silent change to its content.

    Returns "unchanged", "created", or "updated"; exits 1 instead of
    overwriting an existing file whose content would change without `force`.

    An existing generated manifest is a committed artifact, so changing its bytes
    must be explicit, never a side effect of npm prepack. Creating a file where
    none exists overwrites nothing and needs no `force`.
    """
    rendered = json.dumps(data, indent=2) + "\n"
    try:
        current = path.read_text()
    except OSError:
        current = None

    if current == rendered:
        return "unchanged"

    if current is not None and not force:
        print(
            f"Error: refusing to overwrite protected generated file: {path}\n"
            "  Its content differs from what build/gaia.manifest.json generates.\n"
            "  If the manifest change is deliberate, regenerate explicitly:\n"
            "    npm run generate:plugin-root -- --force\n"
            "  A silent rewrite here would apply a hook-configuration change "
            "without an explicit decision behind it.",
            file=sys.stderr,
        )
        sys.exit(1)

    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(path, data)
    return "created" if current is None else "updated"


def write_root_manifests(plugin_name: str, output_dir: Path, *, force: bool = False) -> None:
    """Regenerate the package-root artifacts into an existing directory.

    Writes:
      - <output_dir>/.claude-plugin/plugin.json  (metadata only -- NO inline
        `hooks` block; declaring hooks here as well made CC register them twice)
      - <output_dir>/hooks/hooks.json            (canonical, manifest-derived,
         the SINGLE source of hook declarations; the npm surface reads this via
         merge_local_hooks() in _install_helpers.py)
      - <output_dir>/opencode/agent-inventory.json (manifest-derived agent paths
        used when the source build manifest is absent from a slim npm package)

    It never cleans or copies component files: the target can be the repo root
    itself (`--output-dir .`), and the component files (agents/, skills/,
    hooks/*.py, tools/, config/, bin/) already live there and ship via
    package.json `files[]`.
    """
    manifest = load_manifest(plugin_name)
    output_dir = output_dir.resolve()

    if not output_dir.is_dir():
        print(f"Error: --manifests-only target is not a directory: {output_dir}", file=sys.stderr)
        sys.exit(1)

    # Diagnostics go to stderr: `npm pack` runs this script through `prepack` and
    # forwards the child's stdout into its own, which would corrupt
    # `$(npm pack --silent)` and the JSON array of `npm pack --json`.
    print(f"Regenerating root manifests for plugin '{plugin_name}' in: {output_dir}", file=sys.stderr)

    hooks_json = generate_hooks_json(manifest)
    outcome = _write_generated_manifest(
        output_dir / "hooks" / "hooks.json", hooks_json,
        force=force,
    )
    print(
        f"  hooks/hooks.json: {outcome} ({len(hooks_json['hooks'])} events, "
        f"{len(hooks_json.get('modules', []))} mods)",
        file=sys.stderr,
    )

    plugin_json = generate_plugin_json(manifest)
    outcome = _write_generated_manifest(
        output_dir / ".claude-plugin" / "plugin.json", plugin_json,
        force=force,
    )
    print(
        f"  .claude-plugin/plugin.json: {outcome} (metadata only, no inline hooks)",
        file=sys.stderr,
    )

    agent_inventory = generate_opencode_agent_inventory(manifest)
    outcome = _write_generated_manifest(
        output_dir / "opencode" / "agent-inventory.json", agent_inventory,
        force=force,
    )
    print(
        f"  opencode/agent-inventory.json: {outcome} "
        f"({len(agent_inventory['agents'])} agents)",
        file=sys.stderr,
    )
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
            "Regenerate package-root plugin metadata, hooks, and OpenCode agent inventory "
            "into --output-dir, WITHOUT cleaning or copying component files. Used to make "
            "the repo root (and the published npm tarball root) a valid plugin for source:npm. "
            "Never deletes the output directory. This is the only supported build mode -- "
            "there is no dist/ clean-build path."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Allow overwriting a protected generated artifact whose "
            "content would change. Without it the script refuses and exits 1 -- a "
            "hook-configuration change must be an explicit, attributable command, "
            "never a silent npm-lifecycle side effect."
        ),
    )

    args = parser.parse_args()
    plugin_name = getattr(args, "plugin-name")

    if not args.manifests_only:
        print(
            "Error: --manifests-only is required. The dist/ clean-build path has been "
            "removed -- under source:npm the package root IS the plugin, so this script "
            "only regenerates package-root artifacts in place.",
            file=sys.stderr,
        )
        sys.exit(1)

    output_dir = args.output_dir or REPO_ROOT
    if not output_dir.is_absolute():
        output_dir = REPO_ROOT / output_dir
    write_root_manifests(plugin_name, output_dir, force=args.force)


if __name__ == "__main__":
    main()
