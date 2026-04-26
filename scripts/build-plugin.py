#!/usr/bin/env python3
"""
Build script for gaia plugin ecosystem.

Reads a build manifest and produces a plugin output directory with all
required files, hooks.json, and settings.json.

Usage:
    python3 scripts/build-plugin.py <plugin-name> [--output-dir <path>]

Exit codes:
    0  Build successful
    1  Invalid plugin name or missing manifest
    2  File copy error
"""

import argparse
import json
import shutil
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
VALID_PLUGINS = ("gaia-security", "gaia-ops")

# Directories that "all" resolves to for gaia-ops
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
            entry["hooks"] = [
                {
                    "type": "command",
                    "command": f"${{CLAUDE_PLUGIN_ROOT}}/{entry_point}",
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

    Embeds hooks inline under the 'hooks' key so that CC can load them at
    runtime from the plugin cache, bypassing the install-time path expansion
    bug where ${CLAUDE_PLUGIN_ROOT} is resolved to $CWD/.claude/ instead of
    the plugin cache directory.
    """
    version = manifest.get("version", "0.0.0")
    if version == "from:package.json":
        package_json_path = REPO_ROOT / "package.json"
        with open(package_json_path) as f:
            package_data = json.load(f)
        version = package_data.get("version", "0.0.0")

    plugin_name = manifest["plugin_name"]
    # Homepage anchor differs per plugin; both point to the same repo README.
    homepage_anchors = {
        "gaia-ops": "https://github.com/metraton/gaia-ops#readme",
        "gaia-security": "https://github.com/metraton/gaia-ops#gaia-security",
    }
    homepage = homepage_anchors.get(
        plugin_name, "https://github.com/metraton/gaia-ops#readme"
    )

    # Build inline hooks structure from the same logic as generate_hooks_json().
    # CC docs say plugin.json can declare hooks as a path, array, or inline object.
    # Inline here avoids install-time path expansion via hooks/hooks.json.
    hooks_data = generate_hooks_json(manifest)
    inline_hooks = hooks_data["hooks"]

    return {
        "name": plugin_name,
        "version": version,
        "description": manifest.get("description", ""),
        "author": {
            "name": "jaguilar87",
            "email": "jorge.aguilar87@gmail.com",
        },
        "homepage": homepage,
        "repository": "https://github.com/metraton/gaia-ops",
        "license": "MIT",
        "keywords": ["security", "devops"],
        "engines": {"claude-code": ">=2.1.0"},
        "categories": ["devops", "security", "orchestration"],
        "hooks": inline_hooks,
    }


def generate_settings_json(manifest: dict) -> dict:
    """Generate settings.json from manifest settings configuration."""
    settings = manifest.get("settings", {})
    return {"permissions": settings.get("permissions", {})}


# ---------------------------------------------------------------------------
# Build execution
# ---------------------------------------------------------------------------

def build_plugin(plugin_name: str, output_dir: Path) -> None:
    """Execute the full build for a plugin."""
    print(f"Building plugin: {plugin_name}")
    print(f"Output directory: {output_dir}")
    print(f"Source root: {REPO_ROOT}")
    print()

    # Load manifest
    manifest = load_manifest(plugin_name)

    # Clean output directory
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Resolve and copy files
    source_files = resolve_file_list(manifest)
    copied = 0
    skipped = 0

    for src in source_files:
        if not src.exists():
            print(f"  WARNING: Source file not found: {src.relative_to(REPO_ROOT)}", file=sys.stderr)
            skipped += 1
            continue

        rel = src.relative_to(REPO_ROOT)
        dst = output_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        try:
            shutil.copy2(src, dst)
            copied += 1
        except Exception as e:
            print(f"Error: Failed to copy {rel}: {e}", file=sys.stderr)
            sys.exit(2)

    # Generate hooks.json
    hooks_json = generate_hooks_json(manifest)
    hooks_json_path = output_dir / "hooks" / "hooks.json"
    hooks_json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(hooks_json_path, "w") as f:
        json.dump(hooks_json, f, indent=2)
        f.write("\n")
    print(f"  Generated: hooks/hooks.json ({len(hooks_json['hooks'])} events)")

    # settings.json removed -- CC plugin spec only supports 'agent' and 'subagentStatusLine' keys;
    # our 'permissions' block is non-canonical and CC merges it into workspace settings.local.json.
    # generate_settings_json() is retained above in case a future canonical use case revives it.

    # Generate .claude-plugin/plugin.json
    plugin_json = generate_plugin_json(manifest)
    plugin_json_dir = output_dir / ".claude-plugin"
    plugin_json_dir.mkdir(parents=True, exist_ok=True)
    plugin_json_path = plugin_json_dir / "plugin.json"
    with open(plugin_json_path, "w") as f:
        json.dump(plugin_json, f, indent=2)
        f.write("\n")
    print(f"  Generated: .claude-plugin/plugin.json")

    # Copy per-plugin README.md from build/<plugin>.README.md if present.
    # This README is user-facing inside the installed plugin tarball.
    readme_src = REPO_ROOT / "build" / f"{plugin_name}.README.md"
    if readme_src.exists():
        readme_dst = output_dir / "README.md"
        shutil.copy2(readme_src, readme_dst)
        print(f"  Copied: README.md (from build/{plugin_name}.README.md)")

    # TODO: Re-enable icon copy once Claude Code plugin.json schema documents an 'icon' field (ref: https://code.claude.com/docs/en/plugins-reference)
    # icon_src = REPO_ROOT / "build" / f"{plugin_name}.icon.svg"
    # if icon_src.exists():
    #     icon_dst = output_dir / ".claude-plugin" / "icon.svg"
    #     shutil.copy2(icon_src, icon_dst)
    #     print(f"  Copied: .claude-plugin/icon.svg (from build/{plugin_name}.icon.svg)")

    # Validate output
    errors = validate_output(manifest, output_dir)

    # Print summary
    print()
    print("=" * 60)
    print(f"Build Summary: {plugin_name}")
    print("=" * 60)
    print(f"  Files copied: {copied}")
    print(f"  Files skipped: {skipped}")
    print(f"  Hook events: {len(hooks_json['hooks'])}")
    print(f"  Validation errors: {len(errors)}")

    if errors:
        print()
        print("Validation errors:")
        for err in errors:
            print(f"  - {err}")
        # Don't exit with error for missing optional files
        # The build is still usable

    print()
    print(f"Build complete: {output_dir}")


def validate_output(manifest: dict, output_dir: Path) -> list[str]:
    """Validate the build output structure."""
    errors: list[str] = []

    # Check hook entry points exist in output
    for entry in manifest["hooks"]["entries"]:
        if not (output_dir / entry).exists():
            errors.append(f"Missing hook entry point: {entry}")

    # Check hooks.json exists
    if not (output_dir / "hooks" / "hooks.json").exists():
        errors.append("Missing hooks/hooks.json")

    # settings.json check removed -- file is intentionally not generated (non-canonical per CC plugin spec)

    # Check .claude-plugin/plugin.json exists
    if not (output_dir / ".claude-plugin" / "plugin.json").exists():
        errors.append("Missing .claude-plugin/plugin.json")

    # Check agents
    for agent in manifest.get("agents", []):
        if not (output_dir / agent).exists():
            errors.append(f"Missing agent: {agent}")

    # Check commands
    for cmd in manifest.get("commands", []):
        if not (output_dir / cmd).exists():
            errors.append(f"Missing command: {cmd}")

    return errors


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Build a gaia plugin from its manifest.",
        prog="build-plugin.py",
    )
    parser.add_argument(
        "plugin-name",
        choices=VALID_PLUGINS,
        help="Plugin to build (gaia-security or gaia-ops)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: dist/<plugin-name>)",
    )

    args = parser.parse_args()
    plugin_name = getattr(args, "plugin-name")
    output_dir = args.output_dir or (REPO_ROOT / "dist" / plugin_name)

    # Make output_dir absolute
    if not output_dir.is_absolute():
        output_dir = REPO_ROOT / output_dir

    build_plugin(plugin_name, output_dir)


if __name__ == "__main__":
    main()
