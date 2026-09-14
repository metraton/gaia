"""
Tests for bin/cli/session.py -- gaia session preview.

build_session_context() (hooks/modules/session/session_manifest.py) already
has its own unit tests; these only cover the thin CLI wrapper -- argparse
wiring and dispatch -- so the manifest builder is never re-tested here.
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import cli.session as session_mod


def _build_parser():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="subcommand")
    session_mod.register(subparsers)
    return parser


def test_preview_prints_the_builder_output(monkeypatch, capsys):
    import modules.session.session_manifest as manifest_mod

    monkeypatch.setattr(manifest_mod, "build_session_context", lambda: "## Where I am\n- Machine: host")

    args = _build_parser().parse_args(["session", "preview"])
    exit_code = session_mod.cmd_session(args)

    assert exit_code == 0
    assert "## Where I am" in capsys.readouterr().out


def test_preview_prints_a_placeholder_when_the_manifest_is_empty(monkeypatch, capsys):
    import modules.session.session_manifest as manifest_mod

    monkeypatch.setattr(manifest_mod, "build_session_context", lambda: "")

    args = _build_parser().parse_args(["session", "preview"])
    session_mod.cmd_session(args)

    assert "empty" in capsys.readouterr().out.lower()


def test_bare_session_with_no_action_prints_help_and_fails(capsys):
    args = _build_parser().parse_args(["session"])
    exit_code = session_mod.cmd_session(args)

    assert exit_code == 1
    assert "preview" in capsys.readouterr().out
