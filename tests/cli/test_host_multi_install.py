"""
Tests for host wiring across `gaia install`, `gaia update` and `gaia dev`.

Three properties, one file:

  1. `gaia install` wires exactly the one channel it is given; there is no
     default and no `all`.
  2. When several channels are wired in one run -- `gaia update` re-wiring the
     channels a workspace recorded -- the GLOBAL steps run once and each
     channel is wired after them.
  3. A per-channel failure is named, not fatal: the other channel still wires
     and the command still succeeds. Only an every-channel failure is non-zero.

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

import pytest

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
_BOTH = ("npm", "opencode")


@pytest.fixture(autouse=True)
def isolated_host_policy(tmp_path, monkeypatch, _isolate_gaia_data_dir):
    """Reject external runners and isolate all personal path fallbacks."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GAIA_DB", str(tmp_path / "data" / "gaia.db"))
    monkeypatch.setattr(install_mod.subprocess, "run",
                        lambda *a, **k: pytest.fail("unexpected external runner"))


@pytest.mark.parametrize("host", ["codex", "unknown", "all", None])
def test_internal_invalid_host_fails_before_bootstrap(host):
    with patch.object(install_mod, "_run_bootstrap") as bootstrap:
        with redirect_stderr(io.StringIO()):
            assert install_mod.cmd_install(argparse.Namespace(host=host)) == 1
    bootstrap.assert_not_called()


@pytest.mark.parametrize("register,name", [(register_dev, "dev"), (register_install, "install")])
@pytest.mark.parametrize("host", ["codex", "unknown", "all"])
def test_both_parsers_reject_unknown_host(register, name, host):
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers())
    with pytest.raises(SystemExit), redirect_stderr(io.StringIO()):
        parser.parse_args([name, "--host", host])


def test_claude_code_host_without_path_skips_windows_persistence(tmp_path):
    rc, calls, _, _ = _run_install(tmp_path, host="claude_code", windows=True)
    assert rc == 0
    assert all(step in calls for step in _GLOBAL_STEPS)
    assert "configure_opencode_plugin" not in calls
    assert all(name in calls for name in _CLAUDE_HELPERS)
    # --no-path suppresses machine-level launchers and Windows user environment,
    # not the requested workspace bootstrap, seeds, or host wiring above.
    assert "launcher" not in calls
    assert "workspace_env" not in calls


def _run_install(workspace, *, host=None, channels=None, postinstall=False, failing=(), windows=False,
                 strict=False, failure_action="error"):
    """Run `cmd_install` for *host*, or `install_channels` for *channels*, with every side effect mocked.

    Returns ``(rc, calls, stdout, stderr)`` where *calls* is the ordered trace
    of global steps and host helpers, so run-once and per-channel wiring are
    read off one recording instead of inferred.
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
        strict_wiring=strict,
    )
    calls = []

    def helper(name):
        def call(*_args, **_kwargs):
            calls.append(name)
            if name in failing:
                return {"action": failure_action, "path": "x", "details": "boom"}
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
        patch.object(install_mod, "_is_windows", lambda: windows),
        patch.object(install_mod, "_install_path_launcher", helper("launcher")),
        patch.object(install_mod, "_persist_workspace_env", helper("workspace_env")),
        patch.object(install_mod, "_write_install_error_marker", lambda *a, **k: None),
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
            if channels is None:
                rc = install_mod.cmd_install(ns)
            else:
                rc = install_mod.install_channels(ns, channels)
    finally:
        for p in patches:
            p.stop()
    return rc, calls, out.getvalue(), err.getvalue()


@pytest.mark.parametrize("step,host", [*((s, "claude_code") for s in _CLAUDE_HELPERS),
                                       ("configure_opencode_plugin", "opencode")])
@pytest.mark.parametrize("action", ["error", "skipped"])
def test_strict_wiring_propagates_each_helper_failure(tmp_path, step, host, action):
    rc, calls, out, _ = _run_install(
        tmp_path, host=host, strict=True, failing=(step,), failure_action=action,
    )
    assert rc == 1
    assert step in calls
    assert "Gaia ready" not in out
    assert "launcher" not in calls
    assert "workspace_env" not in calls
    if step in _CLAUDE_HELPERS:
        assert not any(name in calls for name in _CLAUDE_HELPERS[_CLAUDE_HELPERS.index(step) + 1:])


def test_strict_wiring_does_not_fail_soft_for_postinstall(tmp_path):
    rc, _, out, _ = _run_install(tmp_path, host="opencode", strict=True,
                                 postinstall=True, failing=("configure_opencode_plugin",))
    assert rc == 1
    assert "Gaia ready" not in out


class TestChannelChoices(unittest.TestCase):
    def _parse(self, register, argv):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        return parser.parse_args(argv)

    def test_neither_parser_has_a_default(self):
        for register, name in ((register_install, "install"), (register_dev, "dev")):
            args = self._parse(register, [name])
            self.assertIsNone(args.channel)
            self.assertIsNone(args.host)

    def test_both_parsers_offer_the_same_host_aliases(self):
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
        self.assertEqual(choices["install"], install_mod.SUPPORTED_HOSTS)


class TestSingleChannel(unittest.TestCase):
    def test_claude_code_wires_only_claude_code(self):
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
        self.assertIn("export OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true", out)
        self.assertIn("3. Run `gaia doctor` to verify the installation.", out)

    def test_a_lone_failing_channel_is_a_non_zero_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, _, err = _run_install(
                Path(tmp), host="opencode", failing=("configure_opencode_plugin",)
            )

        self.assertEqual(rc, 1)
        self.assertIn("opencode", err)


class TestSeveralChannels(unittest.TestCase):
    def test_global_steps_run_exactly_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, calls, _, _ = _run_install(Path(tmp), channels=_BOTH)

        self.assertEqual(rc, 0)
        for step in _GLOBAL_STEPS:
            self.assertEqual(calls.count(step), 1, f"{step} ran {calls.count(step)} times")

    def test_every_channel_is_wired(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, calls, _, _ = _run_install(Path(tmp), channels=_BOTH)

        self.assertEqual(rc, 0)
        self.assertIn("configure_opencode_plugin", calls)
        self.assertEqual(
            [c for c in calls if c in _CLAUDE_HELPERS], list(_CLAUDE_HELPERS)
        )

    def test_global_steps_precede_every_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, calls, _, _ = _run_install(Path(tmp), channels=_BOTH)

        last_global = max(calls.index(step) for step in _GLOBAL_STEPS)
        first_host = min(
            calls.index(name)
            for name in (*_CLAUDE_HELPERS, "configure_opencode_plugin")
            if name in calls
        )
        self.assertLess(last_global, first_host)

    def test_next_steps_covers_every_wired_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, out, _ = _run_install(Path(tmp), channels=_BOTH)

        self.assertIn("Restart OpenCode to load the Gaia plugin.", out)
        self.assertIn("Open Claude Code in this workspace.", out)
        self.assertEqual(out.count("Run `gaia doctor` to verify the installation."), 1)


class TestPartialChannelFailure(unittest.TestCase):
    def test_a_failing_channel_does_not_stop_the_other(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, calls, _, err = _run_install(
                Path(tmp), channels=_BOTH, failing=("configure_opencode_plugin",)
            )

        self.assertEqual(rc, 0, "one channel failing must not fail the run")
        self.assertEqual(
            [c for c in calls if c in _CLAUDE_HELPERS], list(_CLAUDE_HELPERS)
        )
        self.assertIn("channel configuration failed: opencode", err)

    def test_a_failed_channel_gets_no_restart_instruction(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, out, _ = _run_install(
                Path(tmp), channels=_BOTH, failing=("configure_opencode_plugin",)
            )

        self.assertNotIn("Restart OpenCode", out)
        self.assertIn("Open Claude Code in this workspace.", out)

    def test_every_channel_failing_is_non_zero(self):
        ns = argparse.Namespace(
            postinstall=False, quiet=False, verbose=False, db_path=None,
            skip_workspace=False, no_path=True,
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
                    rc = install_mod.install_channels(ns, _BOTH)

        self.assertEqual(rc, 1)
        # Every channel was still attempted -- the first failure does not abort.
        self.assertEqual(
            [c.args[0] for c in cfg.call_args_list], list(install_mod.SUPPORTED_HOSTS)
        )

    def test_a_failing_step_inside_the_claude_branch_is_not_a_channel_failure(self):
        """The claude branch reports a helper's error and keeps going, so a
        helper failure is not the same event as the channel failing to wire."""
        with tempfile.TemporaryDirectory() as tmp:
            rc, calls, _, _ = _run_install(
                Path(tmp), host="claude_code", failing=("manage_symlinks",)
            )

        self.assertEqual(rc, 0)
        self.assertIn("register_plugin", calls)


class TestRestartWarning(unittest.TestCase):
    def test_each_host_gets_its_own_notice(self):
        self.assertIn("Restart your Claude Code session", _restart_warning("claude_code"))
        self.assertEqual(
            _restart_warning("opencode"),
            "  Restart OpenCode to activate the Gaia plugin and agent configuration.",
        )


class TestRegistryParityTripwire(unittest.TestCase):
    """The CLI host list and the adapter registry are two identity sets.

    `bin/cli/install.py` cannot derive its `choices=` from the registry at
    runtime: `hooks/` ships no `__init__.py`, and `bin/gaia` deliberately
    imports only the one plugin module argv names rather than pulling a
    5k-line adapter into every invocation. This test is the tripwire that
    stands in for that derivation -- registering a host adapter without adding
    it to `SUPPORTED_HOSTS` (or the reverse) fails here.
    """

    def test_supported_hosts_matches_the_adapter_registry(self):
        from adapters.registry import _REGISTRY

        self.assertEqual(
            set(install_mod.SUPPORTED_HOSTS),
            set(_REGISTRY),
            "install.SUPPORTED_HOSTS and adapters.registry._REGISTRY diverged -- "
            "a host with an adapter but no --host value (or the reverse)",
        )

    def test_every_package_channel_wires_a_supported_host(self):
        self.assertEqual(set(install_mod.PACKAGE_CHANNELS.values()), set(install_mod.SUPPORTED_HOSTS))


if __name__ == "__main__":
    unittest.main()
