"""Windows-shaped input read on any platform the way Windows and Git Bash read it."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for entry in (ROOT, ROOT / "bin", ROOT / "hooks"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from cli import _manifest, doctor  # noqa: E402
from gaia import secret_reads  # noqa: E402
from gaia.store.writer import initiative_from_project_ref  # noqa: E402
from modules.security import gaia_cli_only_guard as guard  # noqa: E402
from modules.tools.stage_decomposer import StageDecomposer  # noqa: E402

PUBLISHED_GAIA = r"C:\Users\user\Workspace\node_modules\@jaguilar87\gaia\bin\gaia"


def _words(command):
    return StageDecomposer().decompose(command).stages[0].args


def test_double_quoted_backslash_path_keeps_its_separators():
    assert _words(f'"{PUBLISHED_GAIA}" --version') == [PUBLISHED_GAIA, "--version"]


def test_tokenizer_matches_bash_for_each_quoting():
    assert _words(r"echo 'C:\a\b'") == ["echo", r"C:\a\b"]
    assert _words(r"echo C:\a\b") == ["echo", "C:ab"]
    assert _words(r'echo "a\"b" "c\\d" "e\$f"') == ["echo", 'a"b', r"c\d", "e$f"]


def test_orchestrator_guard_judges_the_published_windows_path_as_written(monkeypatch):
    seen = []
    monkeypatch.setattr(guard, "is_orchestrator_role", lambda payload: True)
    monkeypatch.setattr(guard, "is_trusted_gaia_binary", lambda token: seen.append(token) or token == PUBLISHED_GAIA)
    guard.check(f'"{PUBLISHED_GAIA}" --version', {})
    assert seen == [PUBLISHED_GAIA]


def _base_tokenize_args(self, command_text):
    """df1cc384's StageDecomposer._tokenize_args: every backslash outside single quotes escapes."""
    tokens, current, i, n = [], [], 0, len(command_text)
    in_single = in_double = False
    while i < n:
        ch = command_text[i]
        if ch == "\\" and not in_single and i + 1 < n:
            current.append(command_text[i + 1])
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double and ch in (" ", "\t"):
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(ch)
        i += 1
    if current:
        tokens.append("".join(current))
    return tokens


BACKSLASH_AND_QUOTE_COMMANDS = [
    r'rm -rf "C:\Program Files\x"',
    r"rm -rf C:\Program\ Files\x",
    r'echo "a\"; rm -rf /"',
    r'echo "a\\"; rm -rf /tmp/x',
    r"echo 'a\' ; rm -rf /tmp/x",
    "rm -rf \\\n /tmp/x",
    "git push \\\n origin main",
    'echo "x\\\ny"; git push origin main',
    r'"C:\Program Files\Git\bin\git.exe" push origin main',
    r'"C:\Users\x\kubectl.exe" delete pod "web\1"',
    r'kubectl delete "pod\x"',
    r'bash -c "rm -rf \"C:\x\""',
    r'echo "\$(rm -rf /)"',
    r'echo "$(rm -rf C:\x)"',
    r'curl -X POST "http://example.com\api"',
    r'terraform apply "-var=dir=C:\a"',
    r'sed -i "s/a\/b/c/" C:\x\file.txt',
    r'"C:\Python\python.exe" -c "import shutil; shutil.rmtree(r\"C:\x\")"',
]

_TIERS = ["T0", "T1", "T2", "T3"]


def _strictness(result):
    denied = not result.allowed
    tier = getattr(result.tier, "value", result.tier)
    return (denied, denied and result.block_response is None, _TIERS.index(tier) if tier in _TIERS else 0)


def _clear_classifier_caches():
    from modules.security import mutative_verbs, tiers

    for module in (tiers, mutative_verbs):
        for value in vars(module).values():
            if hasattr(value, "cache_clear"):
                value.cache_clear()


def _verdicts(monkeypatch, tokenizer, check):
    with monkeypatch.context() as patch:
        patch.setattr(StageDecomposer, "_tokenize_args", tokenizer)
        _clear_classifier_caches()
        verdicts = {command: check(command) for command in BACKSLASH_AND_QUOTE_COMMANDS}
    _clear_classifier_caches()
    return verdicts


def test_no_backslash_or_quote_command_classifies_lower_than_on_base(monkeypatch):
    from modules.tools.bash_validator import validate_bash_command

    def check(command):
        return _strictness(validate_bash_command(command))

    base = _verdicts(monkeypatch, _base_tokenize_args, check)
    after = _verdicts(monkeypatch, StageDecomposer._tokenize_args, check)
    assert {command: (base[command], after[command]) for command in base if after[command] < base[command]} == {}


def test_orchestrator_guard_denies_no_less_than_on_base(monkeypatch):
    """Base refused every backslash spelling of the CLI (#44), so its reference is the forward-slash one."""
    forward = PUBLISHED_GAIA.replace("\\", "/")
    monkeypatch.setattr(guard, "is_orchestrator_role", lambda payload: True)
    monkeypatch.setattr(guard, "is_trusted_gaia_binary", lambda token: token in (PUBLISHED_GAIA, forward))

    def denied(binary):
        return lambda tail: not guard.check(f'"{binary}" brief list {tail}', {})[0]

    base = _verdicts(monkeypatch, _base_tokenize_args, denied(forward))
    after = _verdicts(monkeypatch, StageDecomposer._tokenize_args, denied(PUBLISHED_GAIA))
    assert [tail for tail in base if base[tail] and not after[tail]] == []
    assert after[r'echo "a\"; rm -rf /"'] is True
    assert after[r'echo "a\\"; rm -rf /tmp/x'] is True


def test_orchestrator_lane_admits_backslashes_only_in_the_trusted_program_word(monkeypatch):
    monkeypatch.setattr(guard, "is_orchestrator_role", lambda payload: True)
    monkeypatch.setattr(guard, "is_trusted_gaia_binary", lambda token: token == PUBLISHED_GAIA)
    other = r"C:\Users\user\Workspace\node_modules\evil\bin\gaia"

    assert guard.check(f'"{PUBLISHED_GAIA}" brief list', {}) == (True, None)
    assert guard.check(f'"{other}" brief list', {})[0] is False
    for tail in (r'"C:\x"', r"C:\x", r'--json "a\b"', r"'a' \x"):
        assert guard.check(f'"{PUBLISHED_GAIA}" brief list {tail}', {})[0] is False
    for word in (r'C:\x\"', r'C:\$HOME\gaia', r'C:\x\\gaia', "C:\\x`id`\\gaia"):
        assert guard.check(f'"{word}" brief list', {})[0] is False
    assert guard.check(f'"{PUBLISHED_GAIA}"x brief list', {})[0] is False
    assert guard.check(f'"{PUBLISHED_GAIA}" brief list && "{PUBLISHED_GAIA}" brief list', {})[0] is False


def test_doctor_hook_check_keeps_backslashes(tmp_path):
    script = r"C:\Users\user\gaia\hooks\launch.sh"
    problems = doctor._command_problems(f"sh {script}", tmp_path, None)
    assert f"{script} does not exist" in problems


def test_repo_without_remote_gets_the_same_initiative_on_windows():
    assert initiative_from_project_ref(r"C:\Users\user\code\gaia\.git") == "gaia"
    assert initiative_from_project_ref("/home/user/code/gaia/.git") == "gaia"


def test_opencode_uninstall_removes_agents_with_a_backslash_prompt(tmp_path):
    package = r"C:\ws\node_modules\@jaguilar87\gaia"
    config = {
        "plugin": [package + r"\opencode\plugin.ts"],
        "agent": {
            "developer": {"prompt": "{file:" + package + r"\agents\developer.md}"},
            "mine": {"prompt": r"{file:C:\ws\agents\mine.md}"},
        },
    }
    current = {"opencode.json": {"type": "file", "bytes": _manifest._dump_json(config)}}
    stripped = _manifest._json_or_none(_manifest._strip_opencode(tmp_path, current)["opencode.json"]["bytes"])
    assert list(stripped["agent"]) == ["mine"]


def test_secret_read_runner_never_starts_system32_bash(monkeypatch):
    """R3: CreateProcess searches the system directory before PATH, so a bare ``bash`` is WSL's."""
    monkeypatch.setattr(
        secret_reads.shutil, "which",
        lambda name, path=None: path.split(";")[0] + "\\" + name + ".exe" if path else None,
    )
    environ = {"SystemRoot": r"C:\Windows", "PATH": r"C:\Windows\System32;C:\Program Files\Git\usr\bin"}
    assert secret_reads._bash_program(True, environ) == r"C:\Program Files\Git\usr\bin\bash.exe"
    assert secret_reads._bash_program(True, {**environ, "CLAUDE_CODE_GIT_BASH_PATH": r"D:\Git\bin\bash.exe"}) == r"D:\Git\bin\bash.exe"
    assert secret_reads._bash_program(True, {"SystemRoot": r"C:\Windows", "PATH": r"C:\Windows\System32"}) is None
