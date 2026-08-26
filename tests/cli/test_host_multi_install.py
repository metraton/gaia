"""
Tests for `--host all` across `gaia install` and `gaia dev`.

Three properties, one file:

  1. The default is untouched. `--host claude_code` (and no flag at all) wires
     exactly what it wired before, in the same order, with the same output.
  2. `--host all` runs the GLOBAL steps once and wires every supported host.
     The loop lives inside `cmd_install`, after the global steps, which is what
     makes run-once free rather than asserted.
  3. A per-host failure is named, not fatal: the other host still wires and the
     command still succeeds. Only an all-hosts failure is non-zero.

Plus the parity tripwire: the CLI's host list and the hook adapter registry are
two identity sets that must not drift. The CLI cannot import the registry at
runtime (see the note on `install.SUPPORTED_HOSTS`), so the check lives here,
where importing a 5k-line adapter module costs nothing.
"""

import argparse
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[2]
_BIN_DIR = _ROOT / "bin"
_HOOKS_DIR = _ROOT / "hooks"
for _p in (str(_BIN_DIR), str(_HOOKS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cli import install as install_mod  # noqa: E402
from cli.dev import _restart_warning  # noqa: E402
from cli.dev import register as register_dev  # noqa: E402
from cli.install import register as register_install  # noqa: E402
from cli.install import resolve_hosts  # noqa: E402

# The claude_code helpers `_configure_host` drives, in the order it calls them.
_CLAUDE_HELPERS = (
    "configure_settings_json",
    "merge_local_permissions",
    "merge_local_hooks",
    "merge_worktree_settings",
    "manage_symlinks",
    "register_plugin",
)

_GLOBAL_STEPS = ("_run_bootstrap", "_seed_contract_permissions", "_seed_surface_routing")


def _run_install(workspace, *, host, postinstall=False, failing=()):
    """Run `cmd_install` with every side effect mocked.

    Returns ``(rc, calls, stdout, stderr)`` where *calls* is the ordered trace
    of global steps and host helpers, so run-once and per-host wiring are read
    off one recording instead of inferred.
    """
    ns = argparse.Namespace(
        postinstall=postinstall,
        quiet=False,
        verbose=False,
        db_path=None,
        workspace=str(workspace),
        skip_workspace=False,
        no_path=True,
        host=host,
    )
    calls = []

    def helper(name):
        def call(*_args, **_kwargs):
            calls.append(name)
            if name in failing:
                return {"action": "error", "path": "x", "details": "boom"}
            return {"action": "created", "path": "x", "details": "ok"}

        return call

    def global_step(name, result):
        def call(*_args, **_kwargs):
            calls.append(name)
            return result

        return call

    patches = [
        patch.object(install_mod, "_run_bootstrap",
                     global_step("_run_bootstrap", {"rc": 0})),
        patch.object(install_mod, "_seed_contract_permissions",
                     global_step("_seed_contract_permissions",
                                 {"action": "created", "details": "ok"})),
        patch.object(install_mod, "_seed_surface_routing",
                     global_step("_seed_surface_routing",
                                 {"action": "created", "details": "ok"})),
        patch.object(install_mod._install_helpers, "configure_opencode_plugin",
                     helper("configure_opencode_plugin")),
        patch.object(install_mod, "_clear_install_error_marker", lambda *a, **k: None),
        patch.object(install_mod, "_is_windows", lambda: False),
    ]
    patches += [
        patch.object(install_mod._install_helpers, name, helper(name))
        for name in _CLAUDE_HELPERS
    ]

    out, err = io.StringIO(), io.StringIO()
    for p in patches:
        p.start()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = install_mod.cmd_install(ns)
    finally:
        for p in patches:
            p.stop()
    return rc, calls, out.getvalue(), err.getvalue()


class TestResolveHosts(unittest.TestCase):
    def test_all_expands_to_every_supported_host(self):
        self.assertEqual(resolve_hosts("all"), install_mod.SUPPORTED_HOSTS)

    def test_a_named_host_resolves_to_itself(self):
        self.assertEqual(resolve_hosts("claude_code"), ("claude_code",))
        self.assertEqual(resolve_hosts("opencode"), ("opencode",))


class TestHostChoices(unittest.TestCase):
    def _parse(self, register, argv):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        return parser.parse_args(argv)

    def test_install_accepts_all(self):
        self.assertEqual(self._parse(register_install, ["install", "--host", "all"]).host, "all")

    def test_dev_accepts_all(self):
        self.assertEqual(self._parse(register_dev, ["dev", "--host", "all"]).host, "all")

    def test_install_default_is_still_claude_code(self):
        self.assertEqual(self._parse(register_install, ["install"]).host, "claude_code")

    def test_dev_default_is_still_claude_code(self):
        self.assertEqual(self._parse(register_dev, ["dev"]).host, "claude_code")

    def test_both_parsers_offer_the_same_choices(self):
        """One tuple, not two: dev imports install's, so they cannot drift."""
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        install_parser = register_install(subparsers)
        dev_parser = register_dev(subparsers)
        choices = {}
        for name, parser_obj in (("install", install_parser), ("dev", dev_parser)):
            action = next(a for a in parser_obj._actions if a.dest == "host")
            choices[name] = tuple(action.choices)
        self.assertEqual(choices["install"], choices["dev"])
        self.assertEqual(choices["install"], install_mod.HOST_CHOICES)

    def test_an_unsupported_host_is_still_rejected(self):
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                self._parse(register_install, ["install", "--host", "codex"])


class TestSingleHostIsUnchanged(unittest.TestCase):
    """The default path must be indistinguishable from before the change."""

    def test_default_wires_only_claude_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, calls, out, _ = _run_install(Path(tmp), host="claude_code")

        self.assertEqual(rc, 0)
        self.assertNotIn("configure_opencode_plugin", calls)
        self.assertEqual(
            [c for c in calls if c in _CLAUDE_HELPERS], list(_CLAUDE_HELPERS)
        )
        self.assertIn("1. Run `gaia doctor` to verify the installation.", out)
        self.assertIn("2. Open Claude Code in this workspace.", out)

    def test_opencode_alone_wires_only_opencode(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, calls, out, _ = _run_install(Path(tmp), host="opencode")

        self.assertEqual(rc, 0)
        self.assertEqual([c for c in calls if c in _CLAUDE_HELPERS], [])
        self.assertIn("configure_opencode_plugin", calls)
        self.assertIn("1. Restart OpenCode to load the Gaia plugin.", out)
        self.assertIn("2. Run `gaia doctor` to verify the installation.", out)

    def test_a_lone_failing_host_is_still_a_non_zero_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, _, err = _run_install(
                Path(tmp), host="opencode", failing=("configure_opencode_plugin",)
            )

        self.assertEqual(rc, 1)
        self.assertIn("opencode", err)


class TestAllHosts(unittest.TestCase):
    def test_global_steps_run_exactly_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, calls, _, _ = _run_install(Path(tmp), host="all")

        self.assertEqual(rc, 0)
        for step in _GLOBAL_STEPS:
            self.assertEqual(calls.count(step), 1, f"{step} ran {calls.count(step)} times")

    def test_every_host_is_wired(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, calls, _, _ = _run_install(Path(tmp), host="all")

        self.assertEqual(rc, 0)
        self.assertIn("configure_opencode_plugin", calls)
        self.assertEqual(
            [c for c in calls if c in _CLAUDE_HELPERS], list(_CLAUDE_HELPERS)
        )

    def test_global_steps_precede_every_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, calls, _, _ = _run_install(Path(tmp), host="all")

        last_global = max(calls.index(step) for step in _GLOBAL_STEPS)
        first_host = min(
            calls.index(name)
            for name in (*_CLAUDE_HELPERS, "configure_opencode_plugin")
            if name in calls
        )
        self.assertLess(last_global, first_host)

    def test_next_steps_covers_every_wired_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, out, _ = _run_install(Path(tmp), host="all")

        self.assertIn("Restart OpenCode to load the Gaia plugin.", out)
        self.assertIn("Open Claude Code in this workspace.", out)
        self.assertEqual(out.count("Run `gaia doctor` to verify the installation."), 1)


class TestPartialHostFailure(unittest.TestCase):
    def test_a_failing_host_does_not_stop_the_other(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, calls, _, err = _run_install(
                Path(tmp), host="all", failing=("configure_opencode_plugin",)
            )

        self.assertEqual(rc, 0, "one host failing must not fail the install")
        self.assertEqual(
            [c for c in calls if c in _CLAUDE_HELPERS], list(_CLAUDE_HELPERS)
        )
        self.assertIn("opencode", err)
        self.assertNotIn("Restart OpenCode", err)

    def test_the_failing_host_is_named_not_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, _, err = _run_install(
                Path(tmp), host="all", failing=("configure_opencode_plugin",)
            )

        self.assertIn("host configuration failed", err)
        self.assertIn("opencode", err)

    def test_a_failed_host_gets_no_restart_instruction(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, out, _ = _run_install(
                Path(tmp), host="all", failing=("configure_opencode_plugin",)
            )

        self.assertNotIn("Restart OpenCode", out)
        self.assertIn("Open Claude Code in this workspace.", out)

    def test_every_host_failing_is_non_zero(self):
        ns = argparse.Namespace(
            postinstall=False, quiet=False, verbose=False, db_path=None,
            skip_workspace=False, no_path=True, host="all",
        )
        with tempfile.TemporaryDirectory() as tmp:
            ns.workspace = tmp
            with patch.object(install_mod, "_run_bootstrap", return_value={"rc": 0}), \
                 patch.object(install_mod, "_seed_contract_permissions",
                              return_value={"action": "noop", "details": ""}), \
                 patch.object(install_mod, "_seed_surface_routing",
                              return_value={"action": "noop", "details": ""}), \
                 patch.object(install_mod, "_configure_host", return_value=False) as cfg:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    rc = install_mod.cmd_install(ns)

        self.assertEqual(rc, 1)
        # Every host was still attempted -- the first failure does not abort.
        self.assertEqual(
            [c.args[0] for c in cfg.call_args_list], list(install_mod.SUPPORTED_HOSTS)
        )

    def test_a_failing_step_inside_the_claude_branch_is_not_a_host_failure(self):
        """The claude branch reports a helper's error and keeps going, so a
        helper failure is not the same event as the host failing to wire."""
        with tempfile.TemporaryDirectory() as tmp:
            rc, calls, _, _ = _run_install(
                Path(tmp), host="claude_code", failing=("manage_symlinks",)
            )

        self.assertEqual(rc, 0)
        self.assertIn("register_plugin", calls)


class TestRestartWarning(unittest.TestCase):
    def test_single_host_notices_are_unchanged(self):
        self.assertIn("Restart your Claude Code session", _restart_warning("claude_code"))
        self.assertEqual(
            _restart_warning("opencode"),
            "  Restart OpenCode to activate the Gaia plugin and agent configuration.",
        )

    def test_all_warns_about_every_host(self):
        warning = _restart_warning("all")
        self.assertIn("Restart your Claude Code session", warning)
        self.assertIn("Restart OpenCode", warning)

    def test_all_is_expanded_not_treated_as_a_host_name(self):
        """Passing `all` through unresolved would print one host's notice."""
        self.assertNotEqual(_restart_warning("all"), _restart_warning("claude_code"))


class TestRegistryParityTripwire(unittest.TestCase):
    """The CLI host list and the adapter registry are two identity sets.

    `bin/cli/install.py` cannot derive its `choices=` from the registry at
    runtime: `hooks/` ships no `__init__.py`, and `bin/gaia` deliberately
    imports only the one plugin module argv names (bin/gaia:148-161) rather
    than pulling a 5k-line adapter into every invocation. This test is the
    tripwire that stands in for that derivation -- registering a host adapter
    without adding it to `SUPPORTED_HOSTS` (or the reverse) fails here.
    """

    def test_supported_hosts_matches_the_adapter_registry(self):
        from adapters.registry import _REGISTRY

        self.assertEqual(
            set(install_mod.SUPPORTED_HOSTS),
            set(_REGISTRY),
            "install.SUPPORTED_HOSTS and adapters.registry._REGISTRY diverged -- "
            "a host with an adapter but no --host value (or the reverse)",
        )

    def test_the_default_host_agrees_with_the_registry_default(self):
        from adapters.registry import DEFAULT_HOST as ADAPTER_DEFAULT

        self.assertEqual(install_mod.DEFAULT_HOST, ADAPTER_DEFAULT)

    def test_all_is_not_a_registrable_host_name(self):
        """`all` is an expansion keyword; a host actually named `all` would
        make `--host all` ambiguous between one host and every host."""
        from adapters.registry import _REGISTRY

        self.assertNotIn(install_mod.ALL_HOSTS, _REGISTRY)
        self.assertNotIn(install_mod.ALL_HOSTS, install_mod.SUPPORTED_HOSTS)


if __name__ == "__main__":
    unittest.main()
