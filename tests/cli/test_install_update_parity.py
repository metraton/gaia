"""
Parity tests: every responsibility of bin/gaia-update.js MUST be covered by
either bin/cli/install.py or bin/cli/update.py (via _install_helpers.py).

This test is a cross-reference -- it does not exercise behavior end-to-end.
Instead, it asserts that every function in the JS file has a mapped Python
location, and that the mapping is documented in code (so future drift is
visible at review time).

Update the PARITY_MAP below when migrating new behavior. When the JS file
is removed, this test stays green as long as the listed Python locations
keep existing.
"""

import sys
import unittest
from pathlib import Path

_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))


PARITY_MAP = {
    # JS function name -> (python_module_path, python_callable_name)
    "updateSettingsJson":     ("cli._install_helpers", "configure_settings_json"),
    "updateLocalPermissions": ("cli._install_helpers", "merge_local_permissions"),
    "updateLocalHooks":       ("cli._install_helpers", "merge_local_hooks"),
    "updateSymlinks":         ("cli._install_helpers", "manage_symlinks"),
    "plugin-registry write":  ("cli._install_helpers", "register_plugin"),
    "runFreshInstall":        ("cli.install", "cmd_install"),
    "runVerification":        ("cli.update", "_run_verification"),
    "maybeBackfillFts5":      ("cli.doctor", "_apply_fts5_backfill"),
    "main (update path)":     ("cli.update", "cmd_update"),
}


class TestParity(unittest.TestCase):
    def test_every_js_function_has_python_target(self):
        import importlib

        missing = []
        for js_name, (mod_name, fn_name) in PARITY_MAP.items():
            try:
                mod = importlib.import_module(mod_name)
            except ImportError as exc:
                missing.append(f"{js_name}: cannot import {mod_name} ({exc})")
                continue
            if not hasattr(mod, fn_name):
                missing.append(f"{js_name}: {mod_name}.{fn_name} not found")

        self.assertFalse(
            missing,
            "Parity gaps with bin/gaia-update.js:\n  " + "\n  ".join(missing),
        )

    def test_install_and_update_share_helpers(self):
        """install.py and update.py must both import from _install_helpers."""
        from cli import install as install_mod
        from cli import update as update_mod

        # Both modules expose _install_helpers via the imported module attribute
        self.assertTrue(hasattr(install_mod, "_install_helpers"))
        self.assertTrue(hasattr(update_mod, "_install_helpers"))
        # Same module instance -- single source of truth
        self.assertIs(install_mod._install_helpers, update_mod._install_helpers)


if __name__ == "__main__":
    unittest.main()
