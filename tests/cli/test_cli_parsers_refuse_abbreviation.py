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


STANDALONE_SCRIPTS = sorted(
    path.name for path in (REPO_ROOT / "bin" / "cli").glob("*.py")
    if "def _build_standalone_parser" in path.read_text(encoding="utf-8")
)


def test_the_standalone_entry_points_are_the_known_ones():
    assert STANDALONE_SCRIPTS == ["approvals.py", "contract.py"]


@pytest.mark.parametrize("script", STANDALONE_SCRIPTS)
def test_every_standalone_parser_refuses_abbreviation(script, monkeypatch):
    path = REPO_ROOT / "bin" / "cli" / script
    monkeypatch.syspath_prepend(str(REPO_ROOT / "bin"))
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location(f"standalone_{path.stem}_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    walked = list(_walk(module._build_standalone_parser(), (script,)))
    assert len(walked) > 3
    assert [" ".join(path) for path, parser in walked if parser.allow_abbrev] == []


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
