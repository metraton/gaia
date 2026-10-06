"""`gaia release check` from a fresh worktree installs the locked Node
dependencies before it packs or gates anything, through the same frozen,
script-free install `release publish` runs, and only when one is missing.

Every subprocess is answered by a fake at `cli.release._run`, and the gates and
the pack are replaced; nothing here spawns npm.
"""

import json
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli import release  # noqa: E402

_GATES = (
    "gate_pre_publish_validate", "gate_npm_sandbox", "gate_plugin_dryrun",
    "gate_tests", "gate_convergence", "gate_opencode_surface",
)


def _tree(root, *, installed):
    (root / "package.json").write_text(json.dumps({"devDependencies": {"chalk": "^5.3.0"}}))
    (root / "package-lock.json").write_text("{}")
    if installed:
        (root / "node_modules" / "chalk").mkdir(parents=True)
        (root / "node_modules" / "chalk" / "package.json").write_text("{}")
    return root


def _check(root, *, npm_ci=(0, "", "")):
    events = []

    def fake_run(cmd, **kwargs):
        events.append(cmd)
        return npm_ci if cmd[:2] == ["npm", "ci"] else (0, "", "")

    def gate(name):
        def _gate(*args, **kwargs):
            events.append(name)
            return {"name": name, "status": "PASS", "detail": "ok", "duration_ms": 1}
        return _gate

    def pack(*args, **kwargs):
        events.append("pack")
        return {"action": "created", "tarball": root / "gaia.tgz"}

    with ExitStack() as stack:
        stack.enter_context(patch("cli.release._run", side_effect=fake_run))
        stack.enter_context(patch("cli.release._pack_helpers.pack_tarball", side_effect=pack))
        for name in _GATES:
            stack.enter_context(patch(f"cli.release.{name}", side_effect=gate(name)))
        results = release.run_release_check(root)
    return results, events


def test_fresh_tree_installs_locked_deps_before_packing_or_gating(tmp_path):
    """A missing dependency is installed with the frozen, script-free npm ci before the first gate and the pack."""
    results, events = _check(_tree(tmp_path, installed=False))

    assert events[0] == release._NODE_DEPS_INSTALL
    assert {"--ignore-scripts"} <= set(events[0]) and events[0][:2] == ["npm", "ci"]
    assert [e for e in events if isinstance(e, str)][:2] == ["gate_pre_publish_validate", "pack"]
    assert results[0]["name"] == "node deps" and results[0]["status"] == "PASS"
    assert [r["name"] for r in results[1:]] == list(_GATES)


def test_installed_tree_runs_no_install(tmp_path):
    """With every declared dependency present, check spawns no npm install."""
    results, events = _check(_tree(tmp_path, installed=True))

    assert not [e for e in events if isinstance(e, list) and e[:2] == ["npm", "ci"]]
    assert results[0]["name"] == "node deps" and results[0]["status"] == "PASS"


def test_failed_install_stops_before_any_gate(tmp_path):
    """A rejected lockfile fails check alone, since every gate needs the dependencies it could not install."""
    results, events = _check(_tree(tmp_path, installed=False), npm_ci=(1, "", "npm ERR! lockfile out of sync"))

    assert [r["status"] for r in results] == ["FAIL"]
    assert not [e for e in events if isinstance(e, str)]
