"""Red-team probes (handoff 34852) for the orchestrator lane: shell forms refused, literal text kept."""

from __future__ import annotations

import importlib.util
import os
import subprocess
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

_BASE_COMMIT = "50ed1163"
_ORCH = {"agent_type": "", "session_id": "ses-probe", "cwd": str(_REPO_ROOT)}
_ABS = str(_GAIA_BIN)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    (tmp_path / "gaia").symlink_to(_GAIA_BIN)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)


def orch(cmd):
    return BashValidator().validate(cmd, is_subagent=False, session_id="ses-probe",
                                    hook_payload=dict(_ORCH))


def absify(cmd):
    return _ABS + cmd[len("gaia"):] if cmd.startswith("gaia") else cmd


def head(cmd):
    return guard.check(absify(cmd), dict(_ORCH))


def assert_categorical(cmd):
    r = orch(cmd)
    assert not r.allowed, (cmd, r.tier, r.reason)
    assert r.approval_id is None, (cmd, r.approval_id)
    assert r.rule == "floor.gaia-cli-only", (cmd, r.rule, r.reason)
    ok, reason = head(cmd)
    assert not ok and "not approvable" in reason, (cmd, reason)


ADMITTED_BY_BASE = [
    "gaia now 2>/tmp/p", "gaia open plan:94>/tmp/p", "gaia open plan:94 2>/dev/null",
    "gaia now 2>&1", "gaia now 1>&2", "gaia now >&2", "gaia now 3>&1", "gaia now >&-",
    "gaia now 2>&-", "gaia now 9>/tmp/p", "gaia now 10>/tmp/p", "gaia memory search x<<<hi",
    "gaia now {fd}>/tmp/p", "gaia memory search x <<< 'hi there'", "gaia now \\\n>/tmp/p",
    "gaia memory search $'a\\x3eb'", "gaia memory list --workspace ~",
    "gaia memory list --workspace=~/x", "gaia memory search \"$HOME\"",
    "gaia memory search \"${Q}\"", "gaia memory search \"$1\"", "gaia memory search \"$?\"",
    "gaia memory search \"$_\"", "gaia memory search x${IFS}>/tmp/p",
]
TIGHT = ADMITTED_BY_BASE + [
    "gaia now>/tmp/p", "gaia now>>/tmp/p", "gaia now>|/tmp/p", "gaia now<>/tmp/p",
    "gaia now</etc/hostname", "gaia memory list>/tmp/p", "gaia now 2<&0", "gaia now 0<&-",
    "gaia now&>/tmp/p", "gaia now&>>/tmp/p",
    "gaia memory search x <<EOF\nhi\nEOF", "gaia memory search x <<-EOF\n\thi\n\tEOF",
    "gaia memory search x <<'EOF'\nhi\nEOF", "gaia memory search x <<\"EOF\"\nhi\nEOF",
    "gaia memory add --name a --type feedback --body-file - <<'EOF'\nx\nEOF",
    "gaia memory search x <(id)", "gaia memory search x >(cat)", "gaia now\n>/tmp/p",
    "gaia memory search \"`id`\"", "gaia memory search \"`id > /tmp/p`\"",
    "gaia now \"$(true)\" > /tmp/p",
    "gaia contract list # $(\n> /tmp/p", "gaia contract list #$(\n>/tmp/p",
    "gaia contract list # `\n> /tmp/p",
]


@pytest.mark.parametrize("cmd", TIGHT)
def test_tight_and_odd_shell_forms_are_refused(cmd):
    assert_categorical(cmd)


LITERAL_PASS = [
    "gaia memory search 'a>b'", "gaia memory search \"a >> b 2>&1\"",
    "gaia memory search 'x <<EOF'", "gaia memory search '<(id)'",
    "gaia memory search \"a&b\"", "gaia memory search 'a|b; c'",
    "gaia memory search \"shell redirect lane\"", "gaia memory search 'a b' --limit 5",
    "gaia memory search '2>&1'", "gaia memory search '{a,b} *.py ~'",
    "gaia memory search \"{a,b} *.py ~\"", "gaia memory search \"price $\"",
    "gaia memory search \"cost 5$\"", "gaia memory search 'price $5'",
    "gaia memory search \"a $ b\"", "gaia memory list # trailing note",
]


@pytest.mark.parametrize("cmd", LITERAL_PASS)
def test_operator_inside_quotes_stays_t0(cmd):
    r = orch(cmd)
    assert r.allowed and r.tier is SecurityTier.T0_READ_ONLY, (cmd, r.tier, r.rule, r.reason)


@pytest.mark.parametrize("cmd", [
    "gaia open plan:94 '>' /tmp/p", "gaia now '2>&1'", "gaia memory list \">\" /tmp/p",
    "gaia open plan:94 \\> /tmp/p",
])
def test_quoted_operator_word_is_refused_by_arity(cmd):
    ok, reason = head(cmd)
    assert not ok and "positional" in reason, (cmd, reason)


REDIRS = ["> /tmp/p", ">>/tmp/p", "2>&1", "&>/tmp/p", "< /etc/hostname", "<<<x",
          "<<E\nx\nE", ">(cat)", "<(id)", "1>&2"]
PHRASES = sorted(guard.ALLOWED_PHRASES)


def _phrase_cmd(phrase):
    return "gaia " + " ".join(phrase + ("x",) * guard._FIXED_POSITIONALS.get(phrase, 1))


@pytest.mark.parametrize("phrase", PHRASES, ids=" ".join)
def test_every_admitted_phrase_refuses_every_redirect(phrase):
    for redir in REDIRS:
        cmd = f"{_phrase_cmd(phrase)} {redir}"
        ok, reason = head(cmd)
        assert not ok and "not approvable" in reason, (cmd, reason)


@pytest.mark.parametrize("phrase", PHRASES, ids=" ".join)
def test_every_admitted_phrase_redirect_via_validator(phrase):
    assert_categorical(f"{_phrase_cmd(phrase)} > /tmp/p")
    assert_categorical(f"{_phrase_cmd(phrase)} --help > /tmp/p")


def _corpus():
    out = []
    for phrase, (valued, valueless) in guard._FLAG_GRAMMAR.items():
        n = guard._FIXED_POSITIONALS.get(phrase, 1)
        stem = "gaia " + " ".join(phrase + ("'a b'",) * n)
        out.append(stem)
        for f in sorted(valueless):
            out.append(f"{stem} {f}")
            out.append(f"gaia {' '.join(phrase)} {f} " + " ".join(("x",) * n))
        for f in sorted(valued):
            out += [f"{stem} {f} v", f"{stem} {f}=v", f"{stem} {f} 'two words'"]
        out += [f"{stem} --help", f"gaia {' '.join(phrase)} -h"]
    return out


@pytest.fixture(scope="module")
def base_guard(tmp_path_factory):
    shown = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "show",
         f"{_BASE_COMMIT}:hooks/modules/security/gaia_cli_only_guard.py"],
        capture_output=True, text=True)
    if shown.returncode != 0:
        pytest.skip(f"base commit {_BASE_COMMIT} is not in this clone")
    path = tmp_path_factory.mktemp("base") / "base_cli_guard.py"
    path.write_text(shown.stdout)
    spec = importlib.util.spec_from_file_location("modules.security._base_cli_guard", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.is_trusted_gaia_binary = guard.is_trusted_gaia_binary
    return module


def test_no_well_formed_command_the_base_admitted_is_lost(base_guard):
    corpus = _corpus()
    admitted = [c for c in corpus if base_guard.check(absify(c), dict(_ORCH))[0]]
    assert len(admitted) > len(corpus) // 2
    lost = [(c, head(c)[1]) for c in admitted if not head(c)[0]]
    assert not lost, "\n".join(f"{c!r}: {r}" for c, r in lost)


def test_the_base_admitted_the_red_probes(base_guard):
    admitted = [c for c in TIGHT if base_guard.check(absify(c), dict(_ORCH))[0]]
    assert admitted == ADMITTED_BY_BASE


@pytest.mark.parametrize("cmd", [
    "gaia", "gaia --help", "gaia -h", "gaia --version", "gaia memory --help",
    "gaia memory list --help", "gaia install --help", "gaia open --help", "gaia contract view -h",
    "gaia open memory:ws/delete", "gaia open brief:me/apply --line",
    "gaia memory show feedback_delete_apply_push_force", "gaia brief show terraform-destroy",
    "gaia memory search 'kubectl delete pod'", "gaia memory search \"git push --force\"",
    "gaia plan show rm-rf-cleanup",
])
def test_help_and_mutative_word_reads_stay_t0(cmd):
    r = orch(cmd)
    assert r.allowed and r.tier is SecurityTier.T0_READ_ONLY, (cmd, r.tier, r.rule, r.reason)


@pytest.mark.parametrize("cmd", [
    "gaia open -- plan:94 task:1", "gaia memory show -- a b", "gaia memory show a --links b",
    "gaia --json now extra", "gaia now --json=", "gaia memory show a --links=",
    "gaia history -t=1", "gaia contract view --row=1 --contract-id x",
])
def test_arity_and_value_edges_are_refused(cmd):
    ok, reason = head(cmd)
    assert not ok, (cmd, reason)


def test_well_formed_memory_add_keeps_the_memory_skill_floor():
    r = orch("gaia memory add --name feedback_x --type feedback --body y --workspace w")
    assert not r.allowed and r.rule == "floor.memory-skill-not-loaded", (r.rule, r.reason)
