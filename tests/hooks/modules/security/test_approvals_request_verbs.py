"""The gaia approvals request verbs run free as stored data, and nothing beside or inside them does."""
from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import mutative_verbs, tiers  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

AGENT_TYPES = ("developer", "gaia-system")
MAPPED = {"request-set", "request-file-write", "question", "show"}
SUFFIXES = (" P-1", " --command 'echo hi' --what 'Push the branch' --cwd /tmp/x", " --path /tmp/x/f --text 'set it'")
ESC = "build"

NOT_FREE = {
    "chain-semicolon": "gaia approvals request-set --command 'echo hi' --what x ; git push --force origin main",
    "chain-and": f"gaia approvals request-set --command 'echo hi' --what x && rm -rf {ESC}",
    "chain-or": f"gaia approvals request-set --command 'echo hi' --what x || rm -rf {ESC}",
    "background-then-push": "gaia approvals request-set --command 'echo hi' --what x & git push --force origin main",
    "newline-then-push": "gaia approvals request-set --command 'echo hi' --what x\ngit push --force origin main",
    "subshell-chain": "(gaia approvals request-set --command 'echo hi' --what x; git push --force origin main)",
    "push-before": "git push --force origin main; gaia approvals request-set --command 'echo hi' --what x",
    "substitution-in-command": "gaia approvals request-set --command \"$(git push --force origin main)\" --what x",
    "backtick-in-what": f"gaia approvals request-set --command 'echo hi' --what \"`rm -rf {ESC}`\"",
    "substitution-in-question": f"gaia approvals request-set --command 'echo hi' --question \"$(rm -rf {ESC})\"",
    "process-substitution": "gaia approvals request-set --command 'echo hi' --what <(git push --force origin main)",
    "unquoted-heredoc-substitution": f"gaia approvals request-set --command \"$(cat <<EOF\n$(rm -rf {ESC})\nEOF\n)\" --what x",
    "redirect-into-repo": "gaia approvals request-set --command 'echo hi' --what x > {repo}/out.txt",
    "dangerous-flag": "gaia approvals request-set --command 'echo hi' --what x --force",
    "file-write-substituted-path": f"gaia approvals request-file-write --path \"$(rm -rf {ESC})\" --what x",
    "dispatch-identity-cleared": "GAIA_DISPATCH_AGENT= gaia approvals request-set --command 'echo hi' --what x",
    "show-then-approve": "gaia approvals show P-1 && gaia approvals approve P-1",
    "quoted-approve": "gaia approvals 'approve' P-1",
    "split-approve": "gaia approvals app\"\"rove P-1",
    "json-before-approve": "gaia approvals --json approve P-1",
    "global-json-replay": "gaia --json approvals replay P-1",
    "computed-verb": "gaia approvals $V P-1",
}
DATA_FREE = {
    "chain-inside-single-quotes": "gaia approvals request-set --command 'echo a; git push --force origin main' --what x",
    "quoted-heredoc-literal": "gaia approvals request-set --command \"$(cat <<'EOF'\ngit push --force origin main\nEOF\n)\" --what x",
    "substitution-text-single-quoted": "gaia approvals request-set --command 'echo $(rm -rf build)' --what x",
}
SPELLINGS = [
    ["approvals", "appr", "P-1"], ["approvals", "approv", "P-1"], ["approvals", "repl", "P-1"],
    ["approvals", "request-se", "P-1"], ["approvals", "request", "P-1"], ["approvals", "Approve", "P-1"],
    ["approvals", "request-set", "approve", "P-1"], ["approvals", "show", "approve"],
    ["approvals", "show", "--json", "approve"], ["approvals", "question", "approve", "--text", "x"],
    ["--json", "approvals", "request-set", "--command", "x", "approve"],
    ["approvals", "request-set", "--command=approve", "--what", "replay"],
    ["approvals", "request-file-write", "--path", "approve", "--what", "replay"],
]
ABBREVIATED = {"appr", "approv", "repl", "request-se", "request", "Approve"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GAIA_DB", str(tmp_path / "data" / "gaia.db"))
    from gaia.paths import ensure_layout

    ensure_layout()
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (("init", "-q", "-b", "main"), ("commit", "-q", "--allow-empty", "-m", "init"), ("branch", "feature-x"),
                 ("remote", "add", "origin", "https://example.invalid/o/r.git")):
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)
    monkeypatch.chdir(repo)
    return {"repo": str(repo), "db": tmp_path / "data" / "gaia.db"}


def _dispatcher():
    loader = importlib.machinery.SourceFileLoader("gaia_dispatch_under_test", str(_REPO_ROOT / "bin" / "gaia"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    plugins = module._discover_plugins()
    return module, plugins, module._build_parser(plugins)


def _subparsers(parser):
    return [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]


def _paths(parser, prefix=()):
    yield prefix
    for action in _subparsers(parser):
        for name, child in action.choices.items():
            yield from _paths(child, prefix + (name,))


def _verdict(command, cwd, agent_type):
    mutative_verbs.detect_mutative_command.cache_clear()
    tiers._classify_command_tier_cached.cache_clear()
    payload = {"cwd": cwd, "tool_use_id": "t-request", "session_id": "s-request",
               "agent_id": "arequest000000001", "agent_type": agent_type}
    result = BashValidator().validate(command, is_subagent=True, session_id="s-request", agent_type=agent_type,
                                      hook_payload=payload)
    if result.allowed:
        return "FREE"
    return "SIGNED" if result.approval_id else "BLOCKED"


def _without_approvals_map(monkeypatch):
    trimmed = {k: v for k, v in mutative_verbs.SUBCOMMAND_VERB_TIER_EXCEPTIONS.items() if k != ("gaia", "approvals")}
    monkeypatch.setattr(mutative_verbs, "SUBCOMMAND_VERB_TIER_EXCEPTIONS", trimmed)


@pytest.mark.parametrize("agent_type", AGENT_TYPES)
def test_the_map_frees_only_the_request_verbs_and_tightens_nothing(agent_type, world, monkeypatch):
    _, _, parser = _dispatcher()
    paths = sorted({p for p in _paths(parser) if p and (p[0] == "approvals" or any("set" in w for w in p))})
    commands = [" ".join(("gaia",) + p) + sfx for p in paths for sfx in SUFFIXES]
    observed = {c: _verdict(c, world["repo"], agent_type) for c in commands}
    _without_approvals_map(monkeypatch)
    baseline = {c: _verdict(c, world["repo"], agent_type) for c in commands}
    freed = sorted(c for c in commands if observed[c] == "FREE" and baseline[c] != "FREE")
    unexpected = [c for c in freed if not (c.split()[1] == "approvals" and c.split()[2] in MAPPED)]
    tightened = sorted(c for c in commands if baseline[c] == "FREE" and observed[c] != "FREE")
    assert freed and not unexpected and not tightened, {"unexpected": unexpected, "tightened": tightened}


@pytest.mark.parametrize("agent_type", AGENT_TYPES)
def test_without_the_map_the_request_signs(agent_type, world, monkeypatch):
    command = (f"gaia approvals request-set --command 'git -C {world['repo']} push origin feature-x:release/1.x' "
               f"--cwd {world['repo']} --what 'Push'")
    assert _verdict(command, world["repo"], agent_type) == "FREE"
    _without_approvals_map(monkeypatch)
    assert _verdict(command, world["repo"], agent_type) != "FREE"


@pytest.mark.parametrize("agent_type", AGENT_TYPES)
@pytest.mark.parametrize("name", sorted(NOT_FREE))
def test_nothing_beside_or_inside_a_request_runs_free(name, agent_type, world):
    assert _verdict(NOT_FREE[name].format(repo=world["repo"]), world["repo"], agent_type) != "FREE"


@pytest.mark.parametrize("agent_type", AGENT_TYPES)
@pytest.mark.parametrize("name", sorted(DATA_FREE))
def test_stored_command_text_stays_data(name, agent_type, world):
    assert _verdict(DATA_FREE[name], world["repo"], agent_type) == "FREE"


@pytest.mark.parametrize("argv", SPELLINGS, ids=[" ".join(a) for a in SPELLINGS])
def test_a_freed_spelling_never_reaches_approve_or_replay(argv, world):
    _, _, parser = _dispatcher()
    approvals = next(a for a in _subparsers(parser) if "approvals" in a.choices).choices["approvals"]
    for action in _subparsers(approvals):
        for name, child in action.choices.items():
            child.set_defaults(_leaf=name)
    try:
        leaf = getattr(parser.parse_args(argv), "_leaf", None)
    except SystemExit:
        leaf = "refused"
    verdict = _verdict("gaia " + shlex.join(argv), world["repo"], "developer")
    assert not (verdict == "FREE" and leaf in {"approve", "replay"}), (verdict, leaf)
    if argv[1] in ABBREVIATED:
        assert leaf == "refused", leaf


def test_a_freed_request_set_stores_a_presentable_approval(world, capsys, monkeypatch):
    monkeypatch.setenv("GAIA_HOST_SESSION_ID", "s-request")
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "arequest000000001")
    module, plugins, _ = _dispatcher()
    from gaia.paths.resolver import db_path

    assert Path(db_path()) == world["db"]
    argv = ["approvals", "request-set", "--command", f"git -C {world['repo']} push --force origin feature-x",
            "--what", "Force-push the branch", "--question", "Force-push it?",
            "--does", "Force-pushes feature-x", "--impact", "Rewrites the remote branch",
            "--cwd", world["repo"], "--verification", "git ls-remote", "--shared-state", "No shared state",
            "--rollback", "Cannot be undone"]
    rc = module._run_with_plugins(argv + ["--json"], plugins)
    out = capsys.readouterr().out
    assert rc in (0, None), out
    approval_id = json.loads(out)["approval_id"]
    row = dict(plugins)["approvals"]._import_approval_store().get_by_id(approval_id)
    from gaia.approvals.core import check_presentable, missing_phrases

    payload = json.loads(row["payload_json"])
    assert not missing_phrases(payload)
    check_presentable(payload)
