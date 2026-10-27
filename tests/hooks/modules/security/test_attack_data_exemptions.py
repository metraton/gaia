"""The verifier's probes: nothing reaches a secret or an install root through the gaia data exemptions."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(REPO_ROOT), str(REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import mutative_verbs, protected_paths, sensitive_read_guard, tiers  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

AGENT = "a" + "9" * 16
D = f"{AGENT}.bd7e32e1f264"
ADD = f"gaia contract add --draft-id {D} evidence_report.key_outputs"
NOT_FREE = "NOT_FREE"

PROBES = {
    "fake_abs_gaia_heredoc_secret": (
        "{S}/gaia contract fill --json-file - <<'EOF'\ncat ~/.aws/credentials\nEOF", NOT_FREE),
    "relative_gaia_heredoc_secret": (
        "./gaia contract fill --json-file - <<'EOF'\ncat ~/.aws/credentials\nEOF", NOT_FREE),
    "cat_heredoc_piped_to_sh": (
        "cat <<'EOF' | sh\ncat ~/.aws/credentials\nEOF", NOT_FREE),
    "contract_arg_dollar_paren_secret": (
        ADD + ' "$(cat ~/.aws/credentials)"', NOT_FREE),
    "contract_arg_process_subst_secret": (
        ADD + " <(cat ~/.aws/credentials)", NOT_FREE),
    "json_file_credential_path": (
        f"gaia contract fill --draft-id {D} --json-file {{H}}/.aws/credentials", NOT_FREE),
    "memory_body_file_credential_path": (
        "gaia memory add --slug x --body-file {H}/.aws/credentials", NOT_FREE),
    "heredoc_header_carries_credential_file": (
        f"gaia contract fill --draft-id {D} --json-file {{H}}/.aws/credentials --body-file - <<'EOF'\n{{{{}}}}\nEOF",
        NOT_FREE),
    "bare_cat_heredoc_naming_secret": ("cat <<'EOF'\ncat ~/.aws/credentials\nEOF", "FREE"),
}

INSTALL_PROBES = {
    "fake_abs_gaia_heredoc_install_write": (
        "{S}/gaia contract fill --json-file - <<'EOF'\ntouch {I}/bin/evil.py\nEOF", NOT_FREE),
    "contract_arg_dollar_paren_write": (ADD + ' "$(touch {I}/bin/evil.py)"', NOT_FREE),
    "contract_arg_backtick_write": (ADD + ' "`touch {I}/bin/evil.py`"', NOT_FREE),
    "contract_arg_bare_backtick_write": (ADD + " `touch {I}/bin/evil.py`", NOT_FREE),
    "contract_arg_fd_redirect_write": (ADD + ' "x" 2>{I}/bin/evil.py', NOT_FREE),
    "unquoted_delimiter_install_write": (
        f"gaia contract fill --draft-id {D} --json-file - <<EOF\n{{{{\"x\": \"$(touch {{I}}/bin/evil.py)\"}}}}\nEOF",
        NOT_FREE),
    "relative_gaia_contract_install_root": (
        f"./gaia contract add --draft-id {D} evidence_report.key_outputs {{I}}/bin/evil.py", NOT_FREE),
    "contract_arg_single_quoted_substitution_is_literal": (ADD + " '$(touch {I}/bin/evil.py)'", "FREE"),
    "memory_search_naming_root": ("gaia memory search '{I}/bin/gaia'", "FREE"),
}


def _gaia_tree(root: Path) -> Path:
    for sub in ("bin/__pycache__", "gaia", "hooks", "agents"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text('{"name": "@jaguilar87/gaia"}', encoding="utf-8")
    (root / "bin" / "gaia").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    return root


@pytest.fixture
def shell(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / ".aws").mkdir(parents=True)
    (home / ".aws" / "credentials").write_text("[default]\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")
    monkeypatch.setattr("gaia.store.writer.is_harness_session_bound", lambda _s: True)
    protected_paths.reset_caches()
    for fake in (tmp_path / "scratch" / "gaia", tmp_path / "gaia"):
        fake.parent.mkdir(exist_ok=True)
        fake.write_text("#!/bin/sh\nexec sh\n", encoding="utf-8")
        fake.chmod(0o755)

    def verdict(command: str) -> str:
        mutative_verbs.detect_mutative_command.cache_clear()
        mutative_verbs._detect_mutative_command.cache_clear()
        tiers._classify_command_tier_cached.cache_clear()
        payload = {"cwd": str(tmp_path), "agent_id": AGENT, "agent_type": "developer", "session_id": "s-v"}
        result = BashValidator().validate(
            command, is_subagent=True, session_id="s-v", agent_type="developer",
            hook_payload=payload, cwd=str(tmp_path),
        )
        if result.allowed:
            return "FREE"
        return "SIGNED" if result.approval_id else result.rule

    yield verdict, {"H": str(home), "S": str(tmp_path / "scratch")}
    protected_paths.reset_caches()


@pytest.fixture(params=["npm_store", "dev_plugin_cache"])
def install(request, shell, tmp_path):
    verdict, names = shell
    if request.param == "npm_store":
        root = _gaia_tree(tmp_path / "proj" / "node_modules" / "@jaguilar87" / "gaia")
    else:
        root = _gaia_tree(tmp_path / "data" / "cache" / "dev-plugin" / "ws")
    return verdict, {**names, "I": str(root)}


def _holds(got: str, expected: str) -> bool:
    return got != "FREE" if expected == NOT_FREE else got == expected


@pytest.mark.parametrize("name", sorted(PROBES))
def test_secret_probe(shell, name):
    verdict, names = shell
    template, expected = PROBES[name]
    got = verdict(template.format(**names))
    assert _holds(got, expected), f"{name}: {got}"


@pytest.mark.parametrize("command,allowed", [
    ("gaia memory add --slug x --body-file {H}/.aws/credentials", False),
    ("gaia contract fill --json-file={H}/.aws/credentials", False),
    ("gaia contract fill --json-file {S}/payload.json", True),
])
def test_a_gaia_content_file_is_judged_as_a_read(shell, command, allowed):
    _, names = shell
    assert sensitive_read_guard.check(command.format(**names))[0] is allowed


@pytest.mark.parametrize("name", sorted(INSTALL_PROBES))
def test_install_probe(install, name):
    verdict, names = install
    template, expected = INSTALL_PROBES[name]
    got = verdict(template.format(**names))
    assert _holds(got, expected), f"{name}: {got}"
