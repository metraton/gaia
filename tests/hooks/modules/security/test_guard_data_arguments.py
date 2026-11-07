"""What a trusted gaia call stores is data to the path guards; a real write or secret read beside it is not."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(REPO_ROOT), str(REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import mutative_verbs, protected_path_guard, protected_paths, tiers  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

AGENT = "a" + "7" * 16
DRAFT = f"{AGENT}.4df5e309bb03"
ADD = f"gaia contract add --draft-id {DRAFT} evidence_report.key_outputs"
FILL = f"gaia contract fill --draft-id {DRAFT} --json-file - <<'EOF'"

CONTRACT_TEXT_NAMING_A_ROOT = {
    "launcher_command": f'gaia contract add --draft-id {DRAFT} evidence_report.commands_run "{{B}} --version (exit 0)"',
    "root_alone": f'{ADD} "{{I}}"',
    "root_after_equals": f'{ADD} "plugin_root={{I}}"',
    "arrow_before_launcher": f'{ADD} "launcher -> {{B}}"',
    "arrow_glued": f'{ADD} "launcher->{{B}}"',
    "arrow_after_semicolon": f'{ADD} "dev install; launcher -> {{B}}"',
    "pipe_in_value": f'{ADD} "ls {{I}}/bin | wc -l -> 2"',
    "single_quoted": f"gaia contract set --draft-id {DRAFT} conclusion '{{I}}/bin/gaia doctor -> exit 0'",
    "symlink_listing": f'{ADD} "{{B}} -> ../@jaguilar87/gaia/bin/gaia"',
}

REAL_INSTALL_ROOT_WRITES = {
    "plain_touch": "touch {I}/bin/evil.py",
    "redirect_after_data": f'{ADD} "x" > {{I}}/bin/evil.py',
    "chained_touch": f'{ADD} "{{I}}" && touch {{I}}/bin/evil.py',
    "chained_cp_after_arrow": f'{ADD} "a -> b"; cp /tmp/a {{I}}/bin/evil.py',
    "untrusted_gaia_script": f'{{S}}/gaia contract add --draft-id {DRAFT} evidence_report.key_outputs "{{I}}/bin/evil.py"',
    "computed_program": f'$G contract add --draft-id {DRAFT} evidence_report.key_outputs "{{I}}/bin/evil.py"',
    "second_line_writes": f'{ADD} "{{I}}"\ntouch {{I}}/bin/evil.py',
    "chained_after_cd_into_root": f'cd {{I}} && {ADD} "x" && touch bin/evil.py',
}

HEREDOC_DATA_NAMING_A_SECRET = {
    "json_mentions_credentials": FILL + '\n{{"evidence_report": {{"key_outputs": ["probe /root/.aws/credentials_probe_missing -> missing"]}}}}\nEOF',
    "json_line_reads_like_cat": FILL + '\n{{"evidence_report": {{"commands_run": [\ncat ~/.aws/credentials\n]}}}}\nEOF',
    "pretty_printed_path_first": FILL + '\n{{\n  "key_outputs": [\n    "~/.aws/credentials_probe_missing -> missing"\n  ]\n}}\nEOF',
    "json_names_inline_python":FILL + "\n{{\"x\": [\"python3 -c \\\"open('/root/.aws/credentials').read()\\\"\"]}}\nEOF",
}

REAL_SECRET_READS = {
    "python_heredoc": "python3 - <<'EOF'\nprint(open('{H}/.aws/credentials').read())\nEOF",
    "bash_heredoc": "bash <<'EOF'\ncat ~/.aws/credentials\nEOF",
    "sh_s_heredoc": "sh -s <<'EOF'\ncat ~/.aws/credentials\nEOF",
    "gaia_unquoted_delimiter": f"gaia contract fill --draft-id {DRAFT} --json-file - <<EOF\n{{{{\"x\": \"$(cat ~/.aws/credentials)\"}}}}\nEOF",
    "gaia_stdin_from_secret": f"gaia contract fill --draft-id {DRAFT} --json-file - < ~/.aws/credentials",
    "chained_after_data_header": FILL + " && cat ~/.aws/credentials\n{{}}\nEOF",
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
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")
    monkeypatch.setattr("gaia.store.writer.is_harness_session_bound", lambda _s: True)
    protected_paths.reset_caches()
    script = tmp_path / "scratch" / "gaia"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\ntouch \"$5\"\n", encoding="utf-8")
    script.chmod(0o755)

    def verdict(command: str) -> str:
        mutative_verbs.detect_mutative_command.cache_clear()
        mutative_verbs._detect_mutative_command.cache_clear()
        tiers._classify_command_tier_cached.cache_clear()
        payload = {"cwd": str(tmp_path), "agent_id": AGENT, "agent_type": "developer", "session_id": "s-data"}
        result = BashValidator().validate(
            command, is_subagent=True, session_id="s-data", agent_type="developer",
            hook_payload=payload, cwd=str(tmp_path),
        )
        if result.allowed:
            return "FREE"
        return "SIGNED" if result.approval_id else result.rule

    yield verdict, {"H": str(home), "S": str(script.parent)}
    protected_paths.reset_caches()


@pytest.fixture(params=["npm_store", "dev_plugin_cache"])
def install(request, shell, tmp_path):
    if request.param == "npm_store":
        root = _gaia_tree(tmp_path / "proj" / "node_modules" / "@jaguilar87" / "gaia")
        launcher = tmp_path / "proj" / "node_modules" / ".bin" / "gaia"
        launcher.parent.mkdir()
        launcher.symlink_to(root / "bin" / "gaia")
    else:
        root = _gaia_tree(tmp_path / "data" / "cache" / "dev-plugin" / "ws")
        launcher = root / "bin" / "gaia"
    return shell[0], {**shell[1], "I": str(root), "B": str(launcher)}


class TestInstallRootData:
    @pytest.mark.parametrize("name", sorted(CONTRACT_TEXT_NAMING_A_ROOT))
    def test_contract_text_naming_a_root_is_not_a_write(self, install, name):
        verdict, names = install
        assert verdict(CONTRACT_TEXT_NAMING_A_ROOT[name].format(**names)) == "FREE"

    def test_memory_text_naming_a_root_is_not_a_write(self, install):
        _, names = install
        command = 'gaia memory add --slug x --body "{I}/bin/gaia -> {B}"'.format(**names)
        assert protected_path_guard.check(command)[0] is True

    @pytest.mark.parametrize("name", sorted(REAL_INSTALL_ROOT_WRITES))
    def test_a_real_write_beside_it_is_still_refused(self, install, name):
        verdict, names = install
        assert verdict(REAL_INSTALL_ROOT_WRITES[name].format(**names)) == "floor.protected-path"

    def test_a_group_that_is_not_a_data_store_still_names_the_root(self, install):
        _, names = install
        assert protected_path_guard.check("gaia worktree create --repo {I} --project p".format(**names))[0] is False


class TestHeredocData:
    @pytest.mark.parametrize("name", sorted(HEREDOC_DATA_NAMING_A_SECRET))
    def test_a_quoted_body_fed_to_gaia_is_not_a_secret_read(self, shell, name):
        verdict, names = shell
        assert verdict(HEREDOC_DATA_NAMING_A_SECRET[name].format(**names)) == "FREE"

    @pytest.mark.parametrize("name", sorted(REAL_SECRET_READS))
    def test_a_secret_an_interpreter_or_the_shell_reads_is_still_refused(self, shell, name):
        verdict, names = shell
        assert verdict(REAL_SECRET_READS[name].format(**names)) == "floor.secret-read"
