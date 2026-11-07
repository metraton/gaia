"""``--audience`` is retired from ``gaia memory add`` and ``list``: who a row reaches is its kind and owner."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "bin"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


def _parser() -> argparse.ArgumentParser:
    import cli.memory as memory_mod

    parser = argparse.ArgumentParser()
    memory_mod.register(parser.add_subparsers(dest="cmd"))
    return parser


@pytest.mark.parametrize("argv", [
    ["memory", "add", "--name=user_x", "--type=user", "--kind=preference", "--body=b",
     "--audience=executor"],
    ["memory", "list", "--audience=executor"],
])
def test_the_audience_flag_is_refused_by_the_parser(argv):
    with pytest.raises(SystemExit):
        _parser().parse_args(argv)


def test_list_filters_by_kind_instead():
    args = _parser().parse_args(["memory", "list", "--kind=preference"])

    assert args.kind == "preference"
    assert not hasattr(args, "audience")
