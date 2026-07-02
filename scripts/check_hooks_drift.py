#!/usr/bin/env python3
"""Hooks-drift guard: fail if the committed generated hook artifacts diverge
from what the manifest would produce.

Background: ``hooks/hooks.json`` and the inline ``hooks`` object embedded in
``.claude-plugin/plugin.json`` are GENERATED from ``build/gaia.manifest.json``
via ``generate_hooks_json`` (scripts/build-plugin.py), invoked by
``npm run generate:plugin-root``. The manifest is the single source of truth.

If a committed artifact no longer matches the manifest, someone either
hand-edited a generated file or forgot to re-run the generator after editing
the manifest. Either way the published plugin would ship a hook configuration
inconsistent with its own manifest -- e.g. dropping ElicitationResult or
degrading the PreToolUse matcher set. tests/hooks/adapters/test_plugin_manifests
catches the symptom (event set); this guard catches the root cause (drift from
the manifest) at publish time.

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
        expected = generate_hooks_json(manifest).get("hooks", {})
        hooks_json = json.loads(HOOKS_JSON.read_text()).get("hooks", {})
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

    if plugin_hooks != expected:
        drift = True
        print(
            "hooks-drift guard: .claude-plugin/plugin.json inline hooks != "
            "generate_hooks_json(build/gaia.manifest.json)",
            file=sys.stderr,
        )
        _report_diff(".claude-plugin/plugin.json", expected, plugin_hooks)

    if drift:
        print(
            "  Fix: run `npm run generate:plugin-root` and commit the result.",
            file=sys.stderr,
        )
        return 1

    print(f"hook artifacts in sync with manifest ({len(expected)} events)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
