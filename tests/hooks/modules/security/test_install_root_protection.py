"""Every write surface refuses to plant code anywhere inside an installed Gaia root.

An install root is the npm package store (``@jaguilar87/gaia``), the dev-plugin
cache (``<data_dir>/cache/dev-plugin/<id>``), or the root the running hook was
loaded from when that root is not a git checkout. The plain ``gaia`` CLI imports
from these trees, so a planted ``bin/json.py`` or ``.pth`` runs on the next call.
A development checkout is not an install root and stays writable.
"""

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


@pytest.fixture(params=["npm_store", "dev_plugin_cache", "own_root"])
def install_root(request, tmp_path, monkeypatch):
    if request.param == "npm_store":
        return _gaia_tree(tmp_path / "proj" / "node_modules" / "@jaguilar87" / "gaia")
    if request.param == "dev_plugin_cache":
        return _gaia_tree(tmp_path / "data" / "cache" / "dev-plugin" / "ws")
    root = _gaia_tree(tmp_path / "plugins" / "cache" / "gaia-1.0.0")
    monkeypatch.setattr(protected_paths, "_OWN_ROOT", root)
    return root


@pytest.fixture
def dev_checkout(tmp_path, monkeypatch):
    root = _gaia_tree(tmp_path / "work" / "gaia")
    (root / ".git").mkdir()
    monkeypatch.setattr(protected_paths, "_OWN_ROOT", root)
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

    def test_reading_install_code_from_an_interpreter_is_not_a_write(self, install_root):
        assert protected_path_guard.check(f"python3 -c \"print(open('{install_root / 'bin' / 'json.py'}').read())\"")[0] is True


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
