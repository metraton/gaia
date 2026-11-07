#!/usr/bin/env python3
"""Hooks-drift guard: fail if the committed generated hook artifacts diverge
from what the manifest would produce.

``hooks/hooks.json`` is generated from ``build/gaia.manifest.json`` by
``generate_hooks_json`` (scripts/build-plugin.py, run by ``npm run
generate:plugin-root``). This guard asserts that its hooks and its ``modules``
list match the manifest's, and that ``.claude-plugin/plugin.json`` carries no
``hooks`` key, since Claude Code would register every hook twice.

Exit codes: 0 = in sync, 1 = drift detected, 2 = setup/structural error.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "build" / "gaia.manifest.json"
HOOKS_JSON = REPO_ROOT / "hooks" / "hooks.json"
PLUGIN_JSON = REPO_ROOT / ".claude-plugin" / "plugin.json"
BUILD_PLUGIN = REPO_ROOT / "scripts" / "build-plugin.py"


def _load_generate_hooks_json():
    """Import generate_hooks_json from build-plugin.py (hyphenated filename)."""
    spec = importlib.util.spec_from_file_location("_gaia_build_plugin", BUILD_PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.generate_hooks_json


def _report_diff(label: str, expected: dict, actual: dict) -> None:
    exp_events = set(expected)
    act_events = set(actual)
    print(f"  [{label}]", file=sys.stderr)
    if exp_events - act_events:
        print(f"    missing: {sorted(exp_events - act_events)}", file=sys.stderr)
    if act_events - exp_events:
        print(f"    extra:   {sorted(act_events - exp_events)}", file=sys.stderr)
    for ev in sorted(exp_events & act_events):
        if expected[ev] != actual[ev]:
            print(f"    matcher mismatch: {ev}", file=sys.stderr)


def main() -> int:
    for p in (MANIFEST, HOOKS_JSON, PLUGIN_JSON, BUILD_PLUGIN):
        if not p.is_file():
            print(f"hooks-drift guard: required file missing: {p}", file=sys.stderr)
            return 2

    try:
        generate_hooks_json = _load_generate_hooks_json()
        manifest = json.loads(MANIFEST.read_text())
        generated = generate_hooks_json(manifest)
        expected = generated.get("hooks", {})
        expected_mods = generated.get("modules", [])
        committed = json.loads(HOOKS_JSON.read_text())
        hooks_json = committed.get("hooks", {})
        committed_mods = committed.get("modules", [])
        plugin_hooks = json.loads(PLUGIN_JSON.read_text()).get("hooks", {})
    except Exception as exc:  # pragma: no cover - defensive
        print(f"hooks-drift guard: error while loading artifacts: {exc}", file=sys.stderr)
        return 2

    drift = False
    if hooks_json != expected:
        drift = True
        print(
            "hooks-drift guard: hooks/hooks.json != "
            "generate_hooks_json(build/gaia.manifest.json)",
            file=sys.stderr,
        )
        _report_diff("hooks/hooks.json", expected, hooks_json)

    if committed_mods != expected_mods:
        drift = True
        print(
            "hooks-drift guard: hooks/hooks.json 'modules' != the manifest's "
            f"'host_mods' (expected {expected_mods}, found {committed_mods})",
            file=sys.stderr,
        )

    if plugin_hooks:
        drift = True
        print(
            "hooks-drift guard: .claude-plugin/plugin.json embeds an inline "
            "'hooks' block -- hooks must be declared ONLY in hooks/hooks.json. "
            "An inline block makes Claude Code register every hook twice.",
            file=sys.stderr,
        )

    if drift:
        print(
            "  Fix: run `npm run generate:plugin-root` and commit the result.",
            file=sys.stderr,
        )
        return 1

    print(
        f"hook artifacts in sync with manifest "
        f"({len(expected)} events, {len(expected_mods)} mods)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
