"""The orchestrator's Bash runs one gaia invocation: no redirect, no stray value, no extra positional."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GAIA_BIN = _REPO_ROOT / "bin" / "gaia"
_HOOKS_DIR = _REPO_ROOT / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from modules.security import gaia_cli_only_guard as guard  # noqa: E402
from modules.security.tiers import SecurityTier  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

_ORCH = {"agent_type": "", "session_id": "ses-probe", "cwd": str(_REPO_ROOT)}
_SUB = {"agent_id": "a0123456789abcdef", "agent_type": "developer",
        "session_id": "ses-probe", "cwd": str(_REPO_ROOT)}
_ABS = str(_GAIA_BIN)


@pytest.fixture(autouse=True)
def _trusted_gaia_on_path(tmp_path, monkeypatch):
    (tmp_path / "gaia").symlink_to(_GAIA_BIN)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")


def orch(cmd):
    return BashValidator().validate(cmd, is_subagent=False, session_id="ses-probe",
                                    hook_payload=dict(_ORCH))


def sub(cmd):
    return BashValidator().validate(cmd, is_subagent=True, session_id="ses-probe",
                                    agent_type="developer", hook_payload=dict(_SUB))


def guard_alone(cmd):
    if cmd.startswith("gaia "):
        cmd = _ABS + cmd[len("gaia"):]
    return guard.check(cmd, dict(_ORCH))


FREE = [
    "gaia open plan:94",
    "gaia open plan:94 --line",
    "gaia open plan:94 --json",
    "gaia open memory:ws/feedback_push_before_merge --json",
    "gaia now --json",
    "gaia memory list",
    "gaia memory search 'a > b'",
    'gaia memory search "x<y"',
]


@pytest.mark.parametrize("cmd", FREE)
def test_free_shape_is_t0_for_orchestrator(cmd):
    r = orch(cmd)
    assert r.allowed and r.tier is SecurityTier.T0_READ_ONLY, (r.tier, r.reason)
    assert guard_alone(cmd) == (True, None)


REDIRECTS = [
    "> /tmp/probe_out.txt",
    ">/tmp/probe_out.txt",
    ">> /tmp/probe_out.txt",
    "> /home/jorge/probe_out.txt",
    "> /home/jorge/.bashrc",
    "> /home/jorge/.ssh/authorized_keys",
    "> /home/jorge/.gaia/gaia.db",
    ">| /tmp/probe_out.txt",
    "1> /tmp/probe_out.txt",
    "2> /tmp/probe_err.txt",
    "2>> /tmp/probe_err.txt",
    "&> /tmp/probe_out.txt",
    "&>> /tmp/probe_out.txt",
    "2>&1",
    ">&2",
    "3> /tmp/probe_out.txt",
    "< /etc/hostname",
    "< /home/jorge/.ssh/id_rsa",
    "3< /etc/hostname",
    "<&0",
    "<> /tmp/probe_out.txt",
    "<<EOF\nx\nEOF",
    "<<'EOF'\nx\nEOF",
    "<<< x",
    "<(id)",
    ">(cat)",
]
REDIRECTED_VERBS = ["gaia open plan:94", "gaia now --json", "gaia now", "gaia memory list"]


@pytest.mark.parametrize("redirect", REDIRECTS)
@pytest.mark.parametrize("verb", REDIRECTED_VERBS)
def test_every_redirect_is_refused_categorically(verb, redirect):
    cmd = f"{verb} {redirect}"
    r = orch(cmd)
    assert not r.allowed, (r.tier, r.reason)
    assert r.rule == "floor.gaia-cli-only", (r.rule, r.reason)
    allowed, reason = guard_alone(cmd)
    assert not allowed
    assert "not approvable" in reason


DENY_FLAGS = [
    "gaia open plan:94 --census",
    "gaia open plan:94 --since 1d",
    "gaia open plan:94 --bogus",
    "gaia open plan:94 -x",
    "gaia open plan:94 --line=yes",
    "gaia open plan:94 --json=1",
    "gaia open --line=--census plan:94",
    "gaia --json=1 open plan:94",
    "gaia now --json=1",
    "gaia memory list --json=",
]
DENY_POSITIONALS = [
    "gaia open plan:94 task:1",
    "gaia open plan:94 extra --line",
    "gaia open plan:94 -",
    "gaia open --json plan:94 task:1",
    "gaia now extra",
]
DENY_COMPOSITION = [
    "gaia open plan:94 ; id",
    "gaia open plan:94 && id",
    "gaia open plan:94 || id",
    "gaia open plan:94 | cat",
    "gaia open plan:94 $(id)",
    "gaia open plan:94 `id`",
    "gaia open plan:94 &",
    "gaia open plan:94\nid",
]
DENY_META_REF = [
    "gaia open plan:$(id)",
    'gaia open "plan:$(id)"',
    "gaia open plan:94;id",
    "gaia open {--census,plan:94}",
    "gaia open {plan:94,--bogus}",
    "gaia open plan:9[4]",
    "gaia open *",
    "gaia open $HOME",
    "gaia open ${FLAG}",
    "gaia open ~",
]
DENY_ENV = [
    "FOO=1 gaia open plan:94",
    "GAIA_DB=/tmp/x.db gaia open plan:94",
    "env gaia open plan:94",
    "env -i gaia open plan:94",
    "PATH=/tmp gaia open plan:94",
]
DENY_BINARY = [
    "./bin/gaia open plan:94",
    "bin/gaia open plan:94",
    "../checkout/bin/gaia open plan:94",
    f"python3 {_ABS} open plan:94",
]
DENY_ALL = (DENY_FLAGS + DENY_POSITIONALS + DENY_COMPOSITION + DENY_META_REF
            + DENY_ENV + DENY_BINARY)


@pytest.mark.parametrize("cmd", DENY_ALL)
def test_orchestrator_validator_denies(cmd):
    r = orch(cmd)
    assert not r.allowed, (r.tier, r.reason)


@pytest.mark.parametrize("cmd", DENY_ALL)
def test_guard_alone_denies(cmd):
    allowed, reason = guard_alone(cmd)
    assert not allowed, reason


def test_bare_gaia_resolving_to_untrusted_binary_is_denied(tmp_path, monkeypatch):
    fake = tmp_path / "fakebin"
    fake.mkdir()
    (fake / "gaia").write_text("#!/bin/sh\necho pwned\n")
    (fake / "gaia").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}{os.pathsep}/usr/bin:/bin")
    r = orch("gaia open plan:94")
    assert not r.allowed, (r.tier, r.reason)


SUB_T0 = [
    "gaia open memory:ws/delete",
    "gaia open delete:ws/x",
    "gaia open memory:ws/terminate-instances",
    "gaia open memory:ws/run-instances --json",
    "gaia open brief:me/apply --line",
    "gaia open memory:ws/migrate",
    "gaia open memory:ws/resume-point",
    "gaia open memory:ws/rm_-rf",
    "gaia open approval:approve",
    "gaia open memory:ws/push#body",
]


@pytest.mark.parametrize("cmd", SUB_T0)
def test_subagent_mutative_word_ref_is_t0(cmd):
    r = sub(cmd)
    assert r.allowed and r.tier is SecurityTier.T0_READ_ONLY, (r.tier, r.reason)


SUB_NOT_FREE = [
    "gaia open plan:94 ; rm -rf build",
    "gaia open plan:94 && git push origin main --force",
    f"gaia open plan:94 > {_REPO_ROOT}/probe_out.txt",
    "gaia open plan:94 --force",
]


@pytest.mark.parametrize("cmd", SUB_NOT_FREE)
def test_subagent_composition_is_not_free(cmd):
    r = sub(cmd)
    assert not (r.allowed and r.tier is SecurityTier.T0_READ_ONLY), (r.tier, r.reason)


def test_open_admits_exactly_two_flags_and_no_other_phrase_starts_with_open():
    assert guard._KNOWN_SAFE_FLAGS[("open",)] == frozenset({"--line", "--json"})
    assert [p for p in guard.ALLOWED_PHRASES if p[:1] == ("open",)] == [("open",)]
    assert ("open",) not in guard.ALLOWED_WRITE_PHRASES


@pytest.mark.parametrize("cmd,allowed", [
    ("gaia doctor --fix", False),
    ("gaia doctor --fi", False),
    ("gaia now --json", True),
    ("gaia openx plan:94", False),
    ("gaia opens plan:94", False),
    ("gaia memory delete x", False),
    ("gaia install", False),
])
def test_neighbour_verdicts_unchanged(cmd, allowed):
    assert orch(cmd).allowed is allowed
