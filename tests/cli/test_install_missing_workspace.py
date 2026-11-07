"""`gaia install` pointed at a workspace folder that does not exist fails and writes nothing (issue #46)."""

import argparse
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT / "bin"), str(_ROOT / "hooks")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cli import install as install_mod  # noqa: E402
from cli import update as update_mod  # noqa: E402


@pytest.fixture
def data_home(tmp_path, monkeypatch, _isolate_gaia_data_dir):
    """The isolated GAIA_DATA_DIR, with HOME moved into tmp so the user's settings stay out."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("INIT_CWD", raising=False)
    return Path(os.environ["GAIA_DATA_DIR"])


@pytest.fixture
def missing(tmp_path):
    return (tmp_path / "no-such-workspace").resolve()


def _run(module, argv, capsys):
    parser = argparse.ArgumentParser()
    module.register(parser.add_subparsers(dest="subcommand"))
    args = parser.parse_args(argv)
    handler = install_mod.cmd_install if module is install_mod else update_mod.cmd_update
    rc = handler(args)
    return rc, capsys.readouterr().err


@pytest.mark.parametrize("strict", [[], ["--strict-wiring"]], ids=["default", "strict"])
@pytest.mark.parametrize("channel", [[], ["--channel", "npm"], ["--channel", "opencode"]],
                         ids=["detected", "npm", "opencode"])
def test_install_refuses_a_missing_workspace_before_writing(data_home, missing, capsys, channel, strict):
    rc, err = _run(install_mod, ["install", *channel, *strict, "--workspace", str(missing)], capsys)

    assert rc == 1
    assert f"workspace folder {missing} does not exist" in err
    assert list(data_home.iterdir()) == []
    assert not missing.exists()


def test_update_with_a_channel_refuses_a_missing_workspace(data_home, missing, capsys):
    rc, err = _run(update_mod, ["update", "--channel", "npm", "--workspace", str(missing)], capsys)

    assert rc == 1
    assert f"workspace folder {missing} does not exist" in err
    assert list(data_home.iterdir()) == []


@pytest.mark.parametrize("flags,expected_rc,warns", [
    (["--postinstall"], 0, True),
    (["--postinstall", "--strict-wiring"], 1, True),
    (["--skip-workspace"], 0, False),
], ids=["postinstall", "postinstall-strict", "skip-workspace"])
def test_runs_without_a_workspace_still_bootstrap_the_database(data_home, missing, capsys, flags, expected_rc,
                                                               warns):
    rc, err = _run(install_mod, ["install", *flags, "--workspace", str(missing)], capsys)

    assert rc == expected_rc
    assert (f"workspace {missing} does not exist -- skipping configuration" in err) is warns
    assert (data_home / "gaia.db").is_file()
    assert not missing.exists()
