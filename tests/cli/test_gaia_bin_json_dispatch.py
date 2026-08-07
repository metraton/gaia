"""Gap 3 (2026-08-03 CLI-gap fix): the top-level `--json` flag.

Root cause (verified empirically against argparse, not assumed): sharing a
``dest`` between the root parser's ``--json`` and a nested subparser's own
``--json`` is not merely ignored -- ``argparse._SubParsersAction.__call__``
parses each subparser into a FRESH namespace and then unconditionally copies
every attribute of that fresh namespace onto the parent, so a top-level
``gaia --json task list <brief>`` was silently reset back to False by the
child parser's own (unset) ``--json`` default the moment ANY nested parser
along the dispatch path declared that dest.

The fix: the root ``--json`` parses into ``args.global_json`` (a distinct
dest, never colliding with any subcommand's own ``--json``), and
``_apply_global_json()`` folds it into the invoked subcommand's own
``json``/``format`` attribute after parsing -- or reports a clean error when
the resolved subcommand has neither, per "every subcommand either produces
JSON or fails loudly".

``bin/gaia`` has no ``.py`` suffix, so it is loaded via
``importlib.machinery.SourceFileLoader`` (a plain ``spec_from_file_location``
without an explicit loader returns ``None`` for a suffix-less path -- this
was hit and worked around while writing this file).

Matchable by ``pytest tests/ -k gaia_bin_json_dispatch -q``.
"""

from __future__ import annotations

import importlib.util
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))


@pytest.fixture(scope="module")
def gaia_bin():
    loader = SourceFileLoader("_gaia_bin_under_test", str(_BIN_DIR / "gaia"))
    spec = importlib.util.spec_from_loader("_gaia_bin_under_test", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture()
def task_plugins():
    import importlib.util as _u

    spec = _u.spec_from_file_location("cli.task", str(_BIN_DIR / "cli" / "task.py"))
    mod = _u.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return [("task", mod)]


@pytest.fixture()
def paths_plugins():
    import importlib.util as _u

    spec = _u.spec_from_file_location("cli.paths", str(_BIN_DIR / "cli" / "paths.py"))
    mod = _u.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return [("paths", mod)]


# ---------------------------------------------------------------------------
# The clobber bug: --json BEFORE the subcommand must survive to dispatch
# ---------------------------------------------------------------------------

def test_global_json_before_subcommand_is_not_clobbered(gaia_bin, task_plugins):
    parser = gaia_bin._build_parser(task_plugins)
    argv = ["--json", "task", "list", "my-brief"]
    args = parser.parse_args(argv)

    # global_json parses into its OWN dest -- no subparser along the "task
    # list" dispatch chain declares that name, so nothing overwrites it on
    # the way down (unlike the old shared "json" dest, which task/list's own
    # --json action DID overwrite back to its unset default).
    assert args.global_json is True

    err = gaia_bin._apply_global_json(args, argv)
    assert err is None
    assert args.json is True


def test_global_json_forces_json_even_when_local_json_flag_absent_from_argv(
    gaia_bin, task_plugins,
):
    """The exact reported bug: `gaia --json task list <brief>` must resolve
    to args.json is True even though the user never typed a trailing
    --json."""
    parser = gaia_bin._build_parser(task_plugins)
    argv = ["--json", "task", "list", "my-brief"]
    args = parser.parse_args(argv)
    gaia_bin._apply_global_json(args, argv)
    assert args.json is True


def test_local_trailing_json_still_works_without_global_flag(gaia_bin, task_plugins):
    parser = gaia_bin._build_parser(task_plugins)
    argv = ["task", "list", "my-brief", "--json"]
    args = parser.parse_args(argv)
    assert args.global_json is False
    err = gaia_bin._apply_global_json(args, argv)
    assert err is None
    assert args.json is True


def test_no_json_anywhere_leaves_defaults_untouched(gaia_bin, task_plugins):
    parser = gaia_bin._build_parser(task_plugins)
    argv = ["task", "list", "my-brief"]
    args = parser.parse_args(argv)
    err = gaia_bin._apply_global_json(args, argv)
    assert err is None
    assert args.json is False
    assert args.format == "table"


# ---------------------------------------------------------------------------
# Fail loudly when the subcommand has no JSON surface at all
# ---------------------------------------------------------------------------

def test_global_json_on_a_no_json_subcommand_fails_loudly(gaia_bin, paths_plugins):
    """`gaia paths` has neither --json nor --format (verified: bin/cli/paths.py
    defines none). Silently printing the human text would be exactly the bug
    this gap closes -- it must report an error instead."""
    parser = gaia_bin._build_parser(paths_plugins)
    argv = ["--json", "paths"]
    args = parser.parse_args(argv)
    err = gaia_bin._apply_global_json(args, argv)
    assert err is not None
    assert "paths" in err
    assert "no JSON output surface" in err


def test_error_message_names_the_full_verb_chain_not_just_top_level(
    gaia_bin, task_plugins,
):
    """A group-level invocation with no action selected (no json/format
    attrs on the namespace) should name what was actually typed, not just the
    top-level subcommand name."""
    parser = gaia_bin._build_parser(task_plugins)
    argv = ["--json", "task", "gate"]
    args = parser.parse_args(argv)
    err = gaia_bin._apply_global_json(args, argv)
    assert err is not None
    assert "task gate" in err


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
