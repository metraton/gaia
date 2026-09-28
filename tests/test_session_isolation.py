"""The whole pytest session runs with HOME and TMPDIR inside its own isolation root.

A verdict that read the invoker's HOME (a ~/.bashrc, a ~/.gitconfig) or TMPDIR
(one with a .git in an ancestor) would differ between CI, an agent and a
terminal; tests/conftest.py isolates both before any test or worker starts.
"""

import os
import tempfile
from pathlib import Path

import pytest


def _session_root() -> Path:
    root = os.environ.get("GAIA_TEST_SESSION_ROOT")
    assert root, "GAIA_TEST_SESSION_ROOT is unset: the session runs on the invoker's HOME and TMPDIR"
    return Path(root)


def test_home_is_inside_the_session_root_and_not_the_account_home():
    pwd = pytest.importorskip("pwd")
    root = _session_root()
    home = Path(os.environ["HOME"])
    assert home.is_relative_to(root), home
    assert Path.home() == home
    assert home != Path(pwd.getpwuid(os.getuid()).pw_dir)


def test_tmpdir_and_tmp_path_are_inside_the_session_root(tmp_path):
    root = _session_root()
    assert Path(os.environ["TMPDIR"]).is_relative_to(root)
    assert Path(tempfile.gettempdir()).is_relative_to(root)
    assert tmp_path.is_relative_to(root), tmp_path
