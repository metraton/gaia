"""Source selection rejects mutable identities before any installation."""

import argparse
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))

from cli import dev


@pytest.fixture
def source_pair(tmp_path):
    """Create two independent Git sources without involving installed hosts."""
    sources = []
    for name in ("first", "second"):
        source = tmp_path / name
        source.mkdir()
        subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(source), "-c", "user.name=Test",
                        "-c", "user.email=test@example.invalid",
                        "commit", "--allow-empty", "-m", name], check=True, capture_output=True)
        sources.append(source.resolve())
    return sources


def test_exact_source_identity(source_pair):
    first, second = source_pair
    first_commit = dev.validate_source_ref(first, None)
    second_commit = dev.validate_source_ref(second, None)
    assert first_commit != second_commit
    assert dev.validate_source_ref(first, first_commit) == first_commit
    with pytest.raises(ValueError, match="mismatch"):
        dev.validate_source_ref(second, first_commit)


@pytest.mark.parametrize("ref", ["HEAD", "main", "v5.5.0-rc.1", "abc123", "z" * 40, ""])
def test_mutable_or_invalid_ref_never_runs_git(tmp_path, ref):
    with patch.object(dev.subprocess, "run") as run:
        with pytest.raises(ValueError, match="full commit SHA"):
            dev.validate_source_ref(tmp_path, ref)
    run.assert_not_called()


def test_subdirectory_is_not_source_root(source_pair):
    child = source_pair[0] / "child"
    child.mkdir()
    with pytest.raises(ValueError, match="checkout root"):
        dev.validate_source_ref(child, None)


def test_selected_source_reexecutes_with_exact_ref(source_pair, tmp_path):
    source = source_pair[1]
    commit = dev.validate_source_ref(source, None)
    parser = argparse.ArgumentParser()
    dev.register(parser.add_subparsers())
    args = parser.parse_args(["dev", "--from-worktree", str(source), "--ref", commit,
                              "--workspace", str(tmp_path), "--link"])
    with patch.object(dev, "_is_source_checkout", return_value=True), \
         patch.object(dev, "validate_source_ref", return_value=commit), \
         patch.object(dev.subprocess, "run", return_value=subprocess.CompletedProcess([], 7)) as run, \
         patch.object(dev, "_run_pack_mode") as pack, \
         patch.object(dev, "_run_link_mode") as link:
        assert dev.cmd_dev(args) == 7
    assert run.call_args.args[0] == [dev.sys.executable, str(source / "bin/gaia"), "dev",
                                    "--workspace", str(tmp_path), "--ref", commit,
                                    "--mode", "link", "--host", "all"]
    pack.assert_not_called()
    link.assert_not_called()


def test_mismatch_stops_before_installation(source_pair, tmp_path):
    first, second = source_pair
    args = argparse.Namespace(from_worktree=str(second), ref=dev.validate_source_ref(first, None),
                              workspace=str(tmp_path))
    with patch.object(dev, "_is_source_checkout", return_value=True), \
         patch.object(dev, "_run_pack_mode") as pack, \
         patch.object(dev, "_run_link_mode") as link:
        assert dev.cmd_dev(args) == 1
    pack.assert_not_called()
    link.assert_not_called()
