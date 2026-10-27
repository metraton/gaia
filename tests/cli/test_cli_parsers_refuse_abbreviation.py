"""No gaia parser accepts an abbreviated option, so a hook that matches option names whole sees what argparse reads."""

import argparse
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _dispatcher():
    loader = SourceFileLoader("gaia_dispatcher_under_test", str(REPO_ROOT / "bin" / "gaia"))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


def _walk(parser, path=("gaia",)):
    yield path, parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, child in action.choices.items():
                yield from _walk(child, path + (name,))


@pytest.fixture(scope="module")
def parsers():
    dispatcher = _dispatcher()
    return list(_walk(dispatcher._build_parser(dispatcher._discover_plugins())))


def test_every_parser_refuses_abbreviation(parsers):
    assert len(parsers) > 50
    assert [" ".join(path) for path, parser in parsers if parser.allow_abbrev] == []


@pytest.mark.parametrize("argv", [
    ["contract", "fill", "--json-f", "/tmp/x.json"],
    ["memory", "append", "slug", "--body-f", "/tmp/x"],
    ["evidence", "add", "--brief", "b", "--ac", "AC-1", "--type", "file", "--artifact-f", "/tmp/x"],
])
def test_an_abbreviated_file_option_is_an_error(parsers, argv):
    root = parsers[0][1]
    root.parse_args([word.replace("-f", "-file") if word.endswith("-f") else word for word in argv])
    with pytest.raises(SystemExit) as exit_info:
        root.parse_args(argv)
    assert exit_info.value.code == 2
