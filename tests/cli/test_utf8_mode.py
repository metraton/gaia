"""Gaia's Python launches run in UTF-8 mode under a non-UTF-8 inherited locale.

``LC_ALL=C`` with ``PYTHONUTF8=0`` makes Linux decode like a cp1252 Windows
host: the locale codec is ASCII, so a launcher that does not force UTF-8 mode
mangles accented argv, stdin, settings and git output.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEXT = "Decisión: Área → ok"
QUESTION = "¿Publicar la decisión en Área?"


def _legacy_env(tmp_path: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LC_", "PYTHON"))}
    env.update(LC_ALL="C", LANG="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0",
               GAIA_DATA_DIR=str(tmp_path / "data"), GAIA_DB=str(tmp_path / "data" / "gaia.db"))
    return env


def _echo_probe(tmp_path: Path) -> Path:
    probe = tmp_path / "probe.py"
    probe.write_text("import json, sys\n"
                     "print(json.dumps({'utf8': sys.flags.utf8_mode, 'text': sys.stdin.read()}))\n",
                     encoding="utf-8")
    return probe


def _assert_round_trip(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {"utf8": 1, "text": TEXT}


def test_hook_launcher_runs_python_in_utf8_mode(tmp_path):
    result = subprocess.run(["sh", str(ROOT / "hooks" / "launch.sh"), str(_echo_probe(tmp_path))],
                            input=TEXT.encode("utf-8"), capture_output=True,
                            env=_legacy_env(tmp_path), check=False)
    _assert_round_trip(result)


def test_opencode_spawn_env_sets_utf8_mode():
    plugin = (ROOT / "opencode" / "plugin.ts").read_text(encoding="utf-8")
    spawn_envs = re.findall(r"env: \{([^}]*)\}", plugin)
    assert spawn_envs and all('PYTHONUTF8: "1"' in env for env in spawn_envs)


def test_generated_windows_launchers_set_utf8_mode(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "bin"))
    from cli import install

    monkeypatch.setattr(install, "_is_windows", lambda: True)
    workspace = tmp_path / "José"
    install._install_windows_launchers(tmp_path / "gaia", workspace, tmp_path / "bin" / "gaia",
                                       overwrite=True)
    assert 'set "PYTHONUTF8=1"' in (tmp_path / "gaia.cmd").read_text()
    ps1 = (tmp_path / "gaia.ps1").read_bytes()
    assert ps1.startswith(b"\xef\xbb\xbf"), "Windows PowerShell 5.1 reads a BOM-less script as ANSI"
    assert str(workspace).encode("utf-8") in ps1
    assert b'$env:PYTHONUTF8 = "1"' in ps1


def _utf8_decision(env: dict) -> dict:
    """Run bin/gaia's UTF-8 guard with exec stubbed; report whether it restarted and how."""
    code = ("import importlib.machinery, json, sys\n"
            "cli = importlib.machinery.SourceFileLoader('gaia_cli', sys.argv[1]).load_module()\n"
            "calls = []\n"
            "cli.os.execv = lambda exe, argv: calls.append(argv)\n"
            "cli.subprocess.call = lambda argv: calls.append(argv) or 0\n"
            "try:\n    cli._ensure_utf8_mode()\nexcept SystemExit:\n    pass\n"
            "print(json.dumps({'restarted': bool(calls), 'argv': calls[0][1:3] if calls else [],"
            " 'env': cli.os.environ.get('PYTHONUTF8')}))\n")
    result = subprocess.run([sys.executable, "-c", code, str(ROOT / "bin" / "gaia")],
                            capture_output=True, text=True, env=env, check=True)
    return json.loads(result.stdout)


def test_cli_restarts_in_utf8_mode_under_a_legacy_locale(tmp_path):
    decision = _utf8_decision(_legacy_env(tmp_path))
    assert decision == {"restarted": True, "argv": ["-X", "utf8"], "env": "1"}


def test_cli_does_not_restart_under_a_utf8_locale(tmp_path):
    env = _legacy_env(tmp_path)
    env.update(LC_ALL="C.UTF-8", LANG="C.UTF-8")
    assert _utf8_decision(env)["restarted"] is False


def test_cli_under_legacy_locale_stores_and_presents_accented_question(tmp_path):
    env = _legacy_env(tmp_path)
    gaia = [sys.executable, str(ROOT / "bin" / "gaia")]
    request = subprocess.run(
        [*gaia, "approvals", "request-set", "--command", "git push origin feat/x",
         "--cwd", str(tmp_path), "--what", "Publicar la rama con la decisión",
         "--question", QUESTION, "--does", "Publica la rama", "--impact", "Área remota",
         "--verification", "git ls-remote", "--shared-state", "La rama remota",
         "--rollback", "Borrar la rama remota", "--agent-id", "a0123456789abcdef0",
         "--session-id", "utf8-probe", "--json"],
        capture_output=True, env=env, check=False)
    assert request.returncode == 0, request.stderr.decode("utf-8", "replace")
    listing = subprocess.run([*gaia, "approvals", "list", "--json"],
                             capture_output=True, env=env, check=False)
    assert QUESTION in json.dumps(json.loads(listing.stdout), ensure_ascii=False)


def test_doctor_agent_fix_keeps_utf8_settings_bytes(tmp_path):
    settings = tmp_path / ".claude" / "settings.local.json"
    settings.parent.mkdir()
    settings.write_text('{"note": "café"}\n', encoding="utf-8")
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from cli import doctor; "
            "from pathlib import Path; doctor._apply_agent_fix(Path(sys.argv[2]))")
    subprocess.run([sys.executable, "-c", code, str(ROOT / "bin"), str(tmp_path)],
                   env=_legacy_env(tmp_path), check=True)
    assert "café".encode("utf-8") in settings.read_bytes()
    assert json.loads(settings.read_text(encoding="utf-8"))["agent"] == "gaia-orchestrator"


def test_dev_plugin_selection_keeps_utf8_settings_bytes(tmp_path):
    settings = tmp_path / ".claude" / "settings.local.json"
    settings.parent.mkdir()
    settings.write_text('{"note": "café", "enabledPlugins": {}}\n', encoding="utf-8")
    code = ("import sys; sys.path[:0] = sys.argv[1:3]; from cli import _dev_plugin; "
            "from pathlib import Path; r = _dev_plugin.select_dev_plugin(Path(sys.argv[3])); "
            "sys.exit(r['action'] != 'updated')")
    subprocess.run([sys.executable, "-c", code, str(ROOT / "bin"), str(ROOT), str(tmp_path)],
                   env=_legacy_env(tmp_path), check=True)
    assert "café".encode("utf-8") in settings.read_bytes()


def test_shared_runner_decodes_accented_git_output(tmp_path):
    repo = tmp_path / "José"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from gaia.worktree import _git_value; "
            "sys.stdout.buffer.write(_git_value(sys.argv[2], 'rev-parse', '--show-toplevel')"
            ".encode('utf-8'))")
    result = subprocess.run([sys.executable, "-c", code, str(ROOT), str(repo)],
                            capture_output=True, env=_legacy_env(tmp_path), check=False)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout.decode("utf-8").endswith("/José")
