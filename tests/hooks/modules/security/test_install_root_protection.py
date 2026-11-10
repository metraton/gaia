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


def _guard(command, cwd=None):
    allowed, reason = protected_path_guard.check(command, cwd=cwd)
    if allowed and protected_path_guard.install_root_change(command, cwd) is not None:
        return False, "install root change"
    return allowed, reason


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    protected_paths.reset_caches()
    yield
    protected_paths.reset_caches()


@pytest.fixture(params=["npm_store", "dev_plugin_cache", "plugin_cache", "running_hook_root"])
def install_root(request, tmp_path, monkeypatch):
    if request.param == "npm_store":
        return _gaia_tree(tmp_path / "proj" / "node_modules" / "@jaguilar87" / "gaia")
    if request.param == "plugin_cache":
        return _gaia_tree(tmp_path / "cfg" / "plugins" / "cache" / "gaia-marketplace" / "gaia" / "5.7.2")
    if request.param == "running_hook_root":
        from modules.security import mutative_verbs

        root = _gaia_tree(tmp_path / "opt" / "a" / "b" / "gaia")
        monkeypatch.setattr(mutative_verbs, "_HOOK_ROOT", root)
        mutative_verbs._root_is_install.cache_clear()
        return root
    return _gaia_tree(tmp_path / "data" / "cache" / "dev-plugin" / "ws")


@pytest.fixture
def extracted_source(tmp_path):
    return _gaia_tree(tmp_path / "data" / "scratch" / "a1.b2" / "full")


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

    @pytest.mark.parametrize("relative", PLANTED + ("tests/test_probe.py", "hooks/x.py"))
    def test_an_extracted_source_tree_no_host_loads_is_not_an_install(self, extracted_source, relative):
        target = extracted_source / relative
        assert protected_paths.is_protected_hook_path(str(target)) is False
        assert protected_paths.is_install_root_path(str(target)) is False
        assert [form for form in _shell_forms(target) if not _guard(form)[0]] == []

    def test_other_cache_entries_are_not_install_roots(self, tmp_path):
        assert protected_paths.is_protected_hook_path(str(tmp_path / "data" / "cache" / "other" / "x.py")) is False

    def test_a_package_manager_cache_is_not_an_install_container(self, tmp_path):
        cache = tmp_path / ".bun" / "install" / "cache"
        (cache / "@jaguilar87" / "gaia").mkdir(parents=True)
        (cache / "@jaguilar87" / "gaia" / "@T@6e71@@@1").symlink_to(_gaia_tree(cache / "@T@6e71@@@1"))
        store = _gaia_tree(tmp_path / "proj" / "node_modules" / "@jaguilar87" / "gaia").parent.parent
        assert protected_paths.is_install_root_container(str(cache)) is False
        assert protected_paths.is_install_root_container(str(cache / "@jaguilar87")) is False
        assert protected_paths.is_install_root_container(str(store)) is True
        assert protected_paths.is_install_root_container(str(store / "@jaguilar87")) is True


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
        allowed = [form for form in _shell_forms(target) if _guard(form)[0]]
        assert allowed == []

    @pytest.mark.parametrize("relative", PLANTED)
    def test_relative_writes_anchor_at_the_cwd(self, install_root, relative):
        assert _guard(f"echo x > {relative}", cwd=str(install_root))[0] is False
        assert _guard(f"cd {install_root} && cp /tmp/src {relative}")[0] is False

    def test_tar_without_a_directory_extracts_into_the_cwd(self, install_root):
        assert _guard("tar -xf /tmp/src.tar", cwd=str(install_root / "bin"))[0] is False

    @pytest.mark.parametrize("relative", PLANTED)
    def test_development_checkout_stays_writable(self, dev_checkout, relative):
        target = dev_checkout / relative
        refused = [form for form in _shell_forms(target) if not _guard(form)[0]]
        assert refused == []

    def test_an_interpreter_naming_install_code_is_not_provably_read_only(self, install_root):
        assert _guard(f"python3 -c \"print(open('{install_root / 'bin' / 'json.py'}').read())\"")[0] is False

    @pytest.mark.parametrize("form", [
        "cat {I}/bin/json.py",
        "ls -la {I}/bin | wc -l",
        "grep -rn x {I}",
        "find {I} -name '*.py'",
        "git -C {I} status",
        "cd {I} && ls",
        "{I}/bin/gaia doctor",
        "sed -n '1,3p' {I}/package.json",
        "sed -n -e '/name/p' -e '$=' {I}/package.json",
        "awk 'NR<3' {I}/package.json",
        "ls {I}/hooks | sort",
        "sort -u {I}/package.json | uniq -c | cut -c1-80 | tr a-z A-Z | nl",
        "git hash-object {I}/package.json",
        "git -C {I} rev-list --count HEAD",
    ])
    def test_provable_reads_and_running_installed_code_pass(self, install_root, form):
        assert _guard(form.format(I=install_root))[0] is True

    @pytest.mark.parametrize("form", [
        "sed -n '1e touch {I}/bin/x.py' {I}/package.json",
        "sed -n 'w {I}/bin/x.py' {I}/package.json",
        "sed -n 's/a/b/w {I}/bin/x.py' {I}/package.json",
        "sed -n '/a\\/p;/w {I}/bin/x.py/p' {I}/package.json",
        "sed -n '1,3p' -i {I}/package.json",
        "sed -n --in-place '1,3p' {I}/package.json",
        "sed -n -f /tmp/script.sed {I}/package.json",
        "sed -n \"$SCRIPT\" {I}/package.json",
        "sed -n \"$p\" {I}/package.json",
        "p='w {I}/bin/x.py'; sed -n \"$p\" {I}/package.json",
        "Q='1\" \"{{print > \"{I}/bin/x.py\"}}'; awk \"$Q\" {I}/package.json",
        "awk '{{print > \"{I}/bin/x.py\"}}' {I}/package.json",
        "awk 'BEGIN{{system(\"touch {I}/bin/x.py\")}}' {I}/package.json",
        "awk -f /tmp/missing.awk {I}/package.json",
        "awk \"$PROGRAM\" {I}/package.json",
        "sort -o {I}/bin/x.py {I}/package.json",
        "sort --output={I}/bin/x.py {I}/package.json",
        "sort {I}/package.json -o {I}/bin/x.py",
        "sort --compress-program={I}/bin/gaia {I}/package.json",
        "ls {I}/hooks | sort -o {I}/bin/x.py",
        "uniq {I}/package.json {I}/bin/x.py",
        "uniq {I}/package.json -c {I}/bin/x.py",
        "git hash-object -w {I}/package.json",
        "git hash-object -t blob -w {I}/package.json",
    ])
    def test_a_reader_spelled_to_write_or_run_still_asks(self, install_root, form):
        assert _guard(form.format(I=install_root))[0] is False

    @pytest.mark.parametrize("form", [
        "X={I}; ls $X/bin",
        "export X={I}; cat $X/bin/json.py",
        "X={T}; touch $X/a.txt",
        "touch $UNSET/bin/e.py",
        "touch {T}/*.txt",
        "touch {T}/{{a,b}}.txt",
    ])
    def test_variables_and_patterns_away_from_a_root_pass(self, install_root, tmp_path, form):
        assert _guard(form.format(I=install_root, T=tmp_path / "elsewhere"))[0] is True

    @pytest.mark.parametrize("form", ["npm install -g @jaguilar87/gaia", "npm install @jaguilar87/gaia@5.7.0"])
    def test_a_package_name_is_not_an_install_root(self, tmp_path, form):
        assert _guard(form, cwd=str(tmp_path))[0] is True

    @pytest.mark.parametrize("form", [
        "cd {I} && python3 -c \"open('bin/x.py','w').write('x')\"",
        "cd {I}/gaia && python3 -c \"open('../bin/x.py','w').write('x')\"",
        "cd {I} ; node -e \"require('fs').writeFileSync('bin/x.js','x')\"",
    ])
    def test_interpreter_relative_writes_anchor_at_the_cwd_after_cd(self, install_root, form):
        assert protected_path_guard.targets_protected_path(form.format(I=install_root), str(install_root.parent)) is not None

    def test_a_relative_interpreter_write_after_a_computed_cd_is_refused(self, tmp_path):
        reason = _guard("cd \"$X\" && python3 -c \"open('bin/x.py','w')\"", cwd=str(tmp_path))[1]
        assert reason is not None and "cannot place" in reason

    def test_development_checkout_interpreter_write_after_cd_passes(self, dev_checkout):
        assert _guard(f"cd {dev_checkout} && python3 -c \"open('bin/x.py','w')\"")[0] is True


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
    def test_main_session_write_is_sent_to_a_specialist(self, install_root, relative, tool, key):
        verdict = ClaudeCodeAdapter()._write_edit_verdict(
            tool, {key: str(install_root / relative)}, is_subagent=False,
        )
        assert verdict.decision == "deny" and verdict.consent is None
        assert "Dispatch a specialist" in verdict.reason

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
    "var_touch": "X={I}; touch $X/bin/e.py",
    "var_touch_and": "X={I} && touch $X/bin/e.py",
    "var_braced": "X={I}; touch ${{X}}/bin/e.py",
    "var_redirect": "X={I}; echo x > $X/bin/e.py",
    "var_tee": "X={I}; echo x | tee $X/bin/e.py",
    "var_mkdir": "X={I}; mkdir -p $X/gaia/evil",
    "parent_var_touch": "P={P}; touch $P/{BASE}/bin/e.py",
    "unassigned_var_under_parent": "touch {P}/$UNSET/bin/e.py",
    "glob_touch": "touch {GLOB}/bin/e.py",
    "brace_touch": "touch {BRACE}/bin/e.py",
    "glob_redirect": "echo x > {GLOB}/bin/e.py",
}


class TestVariableWriteTargets:
    @pytest.mark.parametrize("form", [
        "echo x > $GAIA_UNSET_Q/bin/x.py", "touch ${GAIA_UNSET_Q}/x", "cp /tmp/a $UNSET_Q/x",
    ])
    def test_an_unresolvable_write_target_is_unprovable(self, tmp_path, form):
        assert protected_path_guard.unprovable_write_target(form, str(tmp_path)) is not None
        assert _guard(form, cwd=str(tmp_path))[0] is True

    @pytest.mark.parametrize("form", [
        "echo x > $(mktemp -d)/x", "cp /tmp/f \"$(date +%s)/x\"", "touch `pwd`/x",
        "echo x > ${OUT:-/tmp}/x", "touch /tmp/$(whoami)/x", "echo x > ${#OUT}/x",
        "echo x > /tmp/$GAIA_UNSET_Q/f", "touch /tmp/${GAIA_UNSET_Q}/f", "echo x | tee /tmp/$UNSET_Q/f",
        "echo x > /tmp/$UNSET_Q/../f", "echo x > $HOME/$UNSET_Q", "echo x > ~/$UNSET_Q/f",
    ])
    def test_a_computed_write_target_is_unprovable(self, tmp_path, form):
        assert protected_path_guard.unprovable_write_target(form, str(tmp_path)) is not None
        assert _guard(form, cwd=str(tmp_path))[0] is True

    @pytest.mark.parametrize("form", [
        "echo x > $(echo {I})/bin/x",
        "cp /tmp/f \"$(dirname {I}/bin/gaia)/x\"",
        "touch `echo {I}`/bin/x",
        "A=({I}/bin/e.py); echo x > ${{A[0]}}",
    ])
    def test_a_computed_target_whose_substitution_names_a_root_is_refused(self, install_root, form):
        assert _guard(form.format(I=install_root))[0] is False

    def test_a_computed_target_asks_through_the_validator(self, subagent, tmp_path):
        from modules.tools.bash_validator import BashValidator

        result = BashValidator().validate(
            "echo x > $(mktemp -d)/x", is_subagent=True, session_id=SESSION,
            agent_type="developer", cwd=str(tmp_path),
            hook_payload={"cwd": str(tmp_path), "agent_id": SUBAGENT_ID, "agent_type": "developer"},
        )
        assert not result.allowed and result.approval_id and "PROTECTED_PATH" not in result.reason

    @pytest.mark.parametrize("form", [
        "echo x > $HOME/x", "touch $TMPDIR/x", "cat $UNSET_Q/x", "X=/tmp; touch $X/a",
        "cat $(echo /tmp/x)", "echo x > /tmp/out.log", "echo x | tee /tmp/out.log",
        "cp /tmp/a ./build/x",
    ])
    def test_a_resolvable_or_read_target_is_not_unprovable(self, monkeypatch, tmp_path, form):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("TMPDIR", str(tmp_path))
        assert protected_path_guard.unprovable_write_target(form, str(tmp_path)) is None

    def test_a_well_known_variable_reaching_an_install_root_is_refused(self, tmp_path):
        _gaia_tree(tmp_path / "data" / "cache" / "dev-plugin" / "ws")
        command = "touch $GAIA_DATA_DIR/cache/dev-plugin/ws/bin/e.py"
        assert _guard(command, cwd=str(tmp_path))[0] is False

    def test_an_unprovable_target_asks_instead_of_refusing(self, subagent, tmp_path):
        from modules.tools.bash_validator import BashValidator

        result = BashValidator().validate(
            "echo x > $GAIA_UNSET_Q/bin/x.py", is_subagent=True, session_id=SESSION,
            agent_type="developer", cwd=str(tmp_path),
            hook_payload={"cwd": str(tmp_path), "agent_id": SUBAGENT_ID, "agent_type": "developer"},
        )
        assert not result.allowed and result.approval_id and "PROTECTED_PATH" not in result.reason


class TestShellAsSubagent:
    """The verifier's probes, run through BashValidator with a subagent's identity."""

    @pytest.mark.parametrize("name", sorted(SHELL_PROBES))
    def test_install_root_shell_writes_never_run_free(self, subagent, install_root, tmp_path, name):
        from modules.tools.bash_validator import BashValidator

        scratch = tmp_path / "scratch"
        scratch.mkdir(exist_ok=True)
        root = str(install_root)
        command = SHELL_PROBES[name].format(
            I=root, S=scratch, P=install_root.parent, BASE=install_root.name,
            GLOB=root[:-1] + "?", BRACE=root[:-1] + "{" + root[-1] + ",zz}",
        )
        result = BashValidator().validate(
            command, is_subagent=True, session_id=SESSION, agent_type="developer",
            hook_payload={"cwd": str(tmp_path), "agent_id": SUBAGENT_ID, "agent_type": "developer"},
            cwd=str(tmp_path),
        )
        assert not result.allowed, result.reason
