"""Every write surface asks or refuses before planting code inside an installed Gaia root."""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "hooks"))

from adapters import tool_policy  # noqa: E402
from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from adapters.types import HookEvent, HookEventType, HostDistribution  # noqa: E402
from modules.security import protected_path_guard, protected_paths  # noqa: E402

PLANTED = ("bin/json.py", "gaia/x.py", "bin/__pycache__/x.pyc", "x.pth")


def _gaia_tree(root: Path) -> Path:
    for sub in ("bin/__pycache__", "gaia", "hooks", "agents"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text('{"name": "@jaguilar87/gaia"}', encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    protected_paths.reset_caches()
    yield
    protected_paths.reset_caches()


@pytest.fixture(params=["npm_store", "dev_plugin_cache"])
def install_root(request, tmp_path):
    if request.param == "npm_store":
        return _gaia_tree(tmp_path / "proj" / "node_modules" / "@jaguilar87" / "gaia")
    return _gaia_tree(tmp_path / "data" / "cache" / "dev-plugin" / "ws")


@pytest.fixture
def dev_checkout(tmp_path):
    root = _gaia_tree(tmp_path / "work" / "gaia")
    (root / ".git").mkdir()
    return root


class TestPredicate:
    @pytest.mark.parametrize("relative", PLANTED)
    def test_planted_code_is_protected(self, install_root, relative):
        assert protected_paths.is_protected_hook_path(str(install_root / relative)) is True

    def test_shipped_agent_definition_is_protected(self, install_root):
        assert protected_paths.is_protected_hook_path(str(install_root / "agents" / "x.md")) is True

    def test_hook_documentation_keeps_its_carve_out(self, install_root):
        assert protected_paths.is_protected_hook_path(str(install_root / "hooks" / "README.md")) is False

    @pytest.mark.parametrize("relative", PLANTED)
    def test_development_checkout_stays_writable(self, dev_checkout, relative):
        assert protected_paths.is_protected_hook_path(str(dev_checkout / relative)) is False

    def test_other_cache_entries_are_not_install_roots(self, tmp_path):
        assert protected_paths.is_protected_hook_path(str(tmp_path / "data" / "cache" / "other" / "x.py")) is False


def _shell_forms(target: Path):
    return [
        f"echo x > {target}",
        f"cp /tmp/src {target}",
        f"mv /tmp/src {target}",
        f"echo x | tee {target}",
        f"sed -i s/a/b/ {target}",
        f"install -m 644 /tmp/src {target}",
        f"ln -s /tmp/src {target}",
        f"rsync /tmp/src {target}",
        f"tar -xf /tmp/src.tar -C {target.parent}",
        f"tar --extract --file=/tmp/src.tar --directory={target.parent}",
        f"python3 -c \"import os; open('{target}', 'w').write('x')\"",
        f"node -e \"require('fs').writeFileSync('{target}', 'x')\"",
        f"python3 - <<'PY'\nopen('{target}', 'w').write('x')\nPY",
    ]


class TestShellSurface:
    @pytest.mark.parametrize("relative", PLANTED)
    def test_every_shell_writer_is_refused(self, install_root, relative):
        target = install_root / relative
        allowed = [form for form in _shell_forms(target) if protected_path_guard.check(form)[0]]
        assert allowed == []

    @pytest.mark.parametrize("relative", PLANTED)
    def test_relative_writes_anchor_at_the_cwd(self, install_root, relative):
        assert protected_path_guard.check(f"echo x > {relative}", cwd=str(install_root))[0] is False
        assert protected_path_guard.check(f"cd {install_root} && cp /tmp/src {relative}")[0] is False

    def test_tar_without_a_directory_extracts_into_the_cwd(self, install_root):
        assert protected_path_guard.check("tar -xf /tmp/src.tar", cwd=str(install_root / "bin"))[0] is False

    @pytest.mark.parametrize("relative", PLANTED)
    def test_development_checkout_stays_writable(self, dev_checkout, relative):
        target = dev_checkout / relative
        refused = [form for form in _shell_forms(target) if not protected_path_guard.check(form)[0]]
        assert refused == []

    def test_an_interpreter_naming_install_code_is_not_provably_read_only(self, install_root):
        assert protected_path_guard.check(f"python3 -c \"print(open('{install_root / 'bin' / 'json.py'}').read())\"")[0] is False

    @pytest.mark.parametrize("form", [
        "cat {I}/bin/json.py",
        "ls -la {I}/bin | wc -l",
        "grep -rn x {I}",
        "find {I} -name '*.py'",
        "git -C {I} status",
        "cd {I} && ls",
        "{I}/bin/gaia doctor",
    ])
    def test_provable_reads_and_running_installed_code_pass(self, install_root, form):
        assert protected_path_guard.check(form.format(I=install_root))[0] is True

    @pytest.mark.parametrize("form", ["npm install -g @jaguilar87/gaia", "npm install @jaguilar87/gaia@5.7.0"])
    def test_a_package_name_is_not_an_install_root(self, tmp_path, form):
        assert protected_path_guard.check(form, cwd=str(tmp_path))[0] is True

    @pytest.mark.parametrize("form", [
        "cd {I} && python3 -c \"open('bin/x.py','w').write('x')\"",
        "cd {I}/gaia && python3 -c \"open('../bin/x.py','w').write('x')\"",
        "cd {I} ; node -e \"require('fs').writeFileSync('bin/x.js','x')\"",
    ])
    def test_interpreter_relative_writes_anchor_at_the_cwd_after_cd(self, install_root, form):
        assert protected_path_guard.targets_protected_path(form.format(I=install_root), str(install_root.parent)) is not None

    def test_a_relative_interpreter_write_after_a_computed_cd_is_refused(self, tmp_path):
        reason = protected_path_guard.check("cd \"$X\" && python3 -c \"open('bin/x.py','w')\"", cwd=str(tmp_path))[1]
        assert reason is not None and "cannot place" in reason

    def test_development_checkout_interpreter_write_after_cd_passes(self, dev_checkout):
        assert protected_path_guard.check(f"cd {dev_checkout} && python3 -c \"open('bin/x.py','w')\"")[0] is True


class TestFileToolSurface:
    @pytest.fixture
    def routed(self, monkeypatch):
        seen = []

        def _capture(self, tool_name, parameters, **_):
            seen.append((tool_name, parameters))
            return tool_policy.PolicyVerdict(decision="deny", reason="captured")

        monkeypatch.setattr(tool_policy.ToolPolicy, "_write_edit_verdict", _capture)
        monkeypatch.setattr(
            "modules.security.approval_grants.cleanup_expired_grants", lambda: None,
        )
        return seen

    @pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit"])
    def test_every_file_writing_tool_reaches_the_gate(self, routed, tool):
        payload = {
            "tool_name": tool,
            "tool_input": {"file_path": "/x/y.py", "notebook_path": "/x/y.ipynb"},
            "agent_id": "a1", "agent_type": "gaia-system", "session_id": "s1",
        }
        event = HookEvent(
            event_type=HookEventType.PRE_TOOL_USE, session_id="s1", payload=payload,
            distribution=HostDistribution(channel="npm"),
        )
        ClaudeCodeAdapter().pre_tool_verdict(event)
        assert [name for name, _ in routed] == [tool]

    @pytest.mark.parametrize("relative", PLANTED)
    @pytest.mark.parametrize("tool,key", [("Write", "file_path"), ("NotebookEdit", "notebook_path")])
    def test_main_session_write_asks(self, install_root, relative, tool, key):
        verdict = ClaudeCodeAdapter()._write_edit_verdict(
            tool, {key: str(install_root / relative)}, is_subagent=False,
        )
        assert verdict.consent is not None

    def test_development_checkout_write_passes(self, dev_checkout):
        verdict = ClaudeCodeAdapter()._write_edit_verdict(
            "Write", {"file_path": str(dev_checkout / "bin" / "json.py")}, is_subagent=False,
        )
        assert verdict.consent is None and verdict.decision != "deny"


SESSION = "ses-install-root"
SUBAGENT_ID = "a" + "7" * 16


@pytest.fixture
def subagent(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    monkeypatch.setattr("gaia.store.writer.is_harness_session_bound", lambda _s: True)
    from modules.security import mutative_verbs, tiers

    mutative_verbs.detect_mutative_command.cache_clear()
    mutative_verbs._detect_mutative_command.cache_clear()
    tiers._classify_command_tier_cached.cache_clear()


def _claude_decision(tool, tool_input):
    payload = {"hook_event_name": "PreToolUse", "session_id": SESSION, "tool_name": tool,
               "tool_input": tool_input, "cwd": "/", "agent_id": SUBAGENT_ID, "agent_type": "developer"}
    adapter = ClaudeCodeAdapter()
    response = adapter.adapt_pre_tool_use(adapter.parse_event(json.dumps(payload)))
    if isinstance(response.output, str):
        return "deny"
    return response.output.get("hookSpecificOutput", {}).get(
        "permissionDecision", "allow" if response.exit_code == 0 else "deny",
    )


def _opencode_decision(tool, args):
    from adapters.opencode import OpenCodeAdapter

    adapter = OpenCodeAdapter()
    event = adapter.parse_event(json.dumps({
        "event": "tool.execute.before", "sessionID": SESSION, "callID": "c1", "agentID": SUBAGENT_ID,
        "roleContext": {"role": "developer", "issuer": "opencode-runtime",
                        "attestation": f"{SESSION}:developer", "verified": True},
        "tool": tool, "args": args,
    }))
    return adapter.adapt_pre_tool_use(event).output.get("action")


class TestHostEntryPoints:
    @pytest.mark.parametrize("relative", PLANTED + ("agents/x.md", "hooks/x.py"))
    @pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit"])
    def test_claude_code_file_tools_do_not_allow_install_writes(self, subagent, install_root, tool, relative):
        target = str(install_root / relative)
        tool_input = {"notebook_path": target, "new_source": "x"} if tool == "NotebookEdit" else {
            "file_path": target, "content": "x", "old_string": "x", "new_string": "y",
            "edits": [{"old_string": "x", "new_string": "y"}],
        }
        assert _claude_decision(tool, tool_input) != "allow"

    @pytest.mark.parametrize("relative", PLANTED)
    @pytest.mark.parametrize("tool", ["write", "edit", "Write", "Edit"])
    @pytest.mark.parametrize("plugin_shape", [False, True])
    def test_opencode_file_tools_do_not_allow_install_writes(self, subagent, install_root, tool, relative, plugin_shape):
        target = str(install_root / relative)
        args = {"filePath": target, "content": "x", "oldString": "x", "newString": "y"}
        if plugin_shape:
            args["file_path"] = target
        assert _opencode_decision(tool, args) not in ("allow", None, "")


SHELL_PROBES = {
    "cd_redirect": "cd {I} && echo x > bin/evil.py",
    "cd_python_inline": "cd {I}/bin && python3 -c \"open('evil.py','w').write('x')\"",
    "cd_python_heredoc": "cd {I} && python3 - <<'PY'\nopen('bin/evil.py','w').write('x')\nPY",
    "dotdot_abs": "echo x > {I}/../gaia/bin/evil.py",
    "hardlink_from_install": "ln {I}/bin/json.py {S}/h.py",
    "pyc_compileall": "python3 -m compileall {I}/bin",
    "pyc_py_compile": "python3 -m py_compile {I}/bin/json.py",
    "tar_attached_C": "tar -xf /tmp/a.tar -C{I}",
    "tar_directory": "tar -xf /tmp/a.tar --directory {I}",
    "unzip_d": "unzip -o /tmp/a.zip -d {I}",
    "cd_unzip": "cd {I} && unzip -o /tmp/a.zip",
    "git_apply_directory": "git apply --directory={I} /tmp/p.diff",
    "cd_git_apply": "cd {I} && git apply /tmp/p.diff",
    "git_checkout_worktree": "git --work-tree={I} checkout HEAD -- .",
    "patch_d": "patch -p1 -d {I} -i /tmp/p.diff",
    "patch_file": "patch {I}/bin/json.py /tmp/p.diff",
    "py_concat": "python3 -c \"import shutil; shutil.copy('/tmp/a', '{P}' + '/gaia/bin/evil.py')\"",
    "dd_of": "dd if=/tmp/a of={I}/bin/evil.py",
    "truncate": "truncate -s 0 {I}/bin/json.py",
    "mkdir_abs": "mkdir -p {I}/gaia/evilpkg",
    "perl_inline": "perl -e 'open(F, \">{I}/bin/evil.py\")'",
    "find_exec_cp": "find /tmp/a -exec cp {{}} {I}/bin/evil.py ;",
    "xargs_touch": "echo {I}/bin/evil.py | xargs touch",
}


class TestShellAsSubagent:
    """The verifier's probes, run through BashValidator with a subagent's identity."""

    @pytest.mark.parametrize("name", sorted(SHELL_PROBES))
    def test_install_root_shell_writes_are_refused_categorically(self, subagent, install_root, tmp_path, name):
        from modules.tools.bash_validator import BashValidator

        scratch = tmp_path / "scratch"
        scratch.mkdir(exist_ok=True)
        command = SHELL_PROBES[name].format(I=install_root, S=scratch, P=install_root.parent)
        result = BashValidator().validate(
            command, is_subagent=True, session_id=SESSION, agent_type="developer",
            hook_payload={"cwd": str(tmp_path), "agent_id": SUBAGENT_ID, "agent_type": "developer"},
            cwd=str(tmp_path),
        )
        assert not result.allowed and "PROTECTED_PATH" in result.reason
