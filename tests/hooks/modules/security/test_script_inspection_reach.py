"""Reach of the script-file inspection lane: prefix runners and direct JS mutation.

Two gaps left the content-inspection layer unreachable for real invocations:

* A prefix runner (``uv run``, ``poetry run``, ``pipx run``, ``npx``) is not an
  interpreter, so ``_check_script_file`` returned None and the wrapped script
  was never opened -- ``uv run x.py`` classified T0 while ``python3 x.py``
  classified T3 on the same file.
* The lexer lane ran only the exec-sink detector, so a filesystem mutation that
  never reaches the shell (``fs.rmSync``) classified T0 inside ``node x.js``
  while the identical call under ``node -e`` classified T3.

Each class is pinned by a NEGATIVE test -- a case that classified T0 before the
lane existed and must classify T3 now -- plus the false-positive guards that
keep the widening from gating ordinary tooling.
"""
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402


PY_COPY_BODY = 'import shutil\nshutil.copy("/tmp/a", "/tmp/b")\n'
JS_RM_BODY = 'const fs = require("fs");\nfs.rmSync("/tmp/t", { recursive: true });\n'


@pytest.fixture
def py_copy_script(tmp_path):
    script = tmp_path / "c.py"
    script.write_text(PY_COPY_BODY)
    return script


@pytest.fixture
def js_rm_script(tmp_path):
    script = tmp_path / "r.js"
    script.write_text(JS_RM_BODY)
    return script


class TestPrefixRunnerReDispatch:
    """``<runner> run <script>`` must classify as the unwrapped invocation."""

    def test_uv_run_python_script_is_mutative(self, py_copy_script):
        # The negative proof: this classified T0 (safe by elimination) because
        # `uv` is not an interpreter, so the AST lane never saw shutil.copy.
        result = detect_mutative_command(f"uv run {py_copy_script}")
        assert result.is_mutative is True
        assert result.verb == "shutil-copy"

    def test_uv_run_matches_direct_interpreter_verdict(self, py_copy_script):
        wrapped = detect_mutative_command(f"uv run {py_copy_script}")
        direct = detect_mutative_command(f"python3 {py_copy_script}")
        assert wrapped.is_mutative == direct.is_mutative
        assert wrapped.verb == direct.verb

    @pytest.mark.parametrize("runner", ["uv run", "poetry run", "pipx run"])
    def test_every_python_runner_reaches_the_ast_lane(self, runner, py_copy_script):
        result = detect_mutative_command(f"{runner} {py_copy_script}")
        assert result.is_mutative is True

    def test_runner_with_explicit_interpreter_token(self, py_copy_script):
        result = detect_mutative_command(f"uv run python {py_copy_script}")
        assert result.is_mutative is True
        assert result.verb == "shutil-copy"

    def test_runner_value_flag_does_not_swallow_the_script(self, py_copy_script):
        # `--with requests` consumes its value; without that knowledge the value
        # would be mistaken for the wrapped command and the script never read.
        result = detect_mutative_command(f"uv run --with requests {py_copy_script}")
        assert result.is_mutative is True
        assert result.verb == "shutil-copy"

    def test_relative_script_resolves_against_cwd(self, tmp_path, py_copy_script):
        result = detect_mutative_command("uv run c.py", cwd=str(tmp_path))
        assert result.is_mutative is True
        assert result.verb == "shutil-copy"

    def test_runner_on_js_script_reaches_the_lexer_lane(self, js_rm_script):
        result = detect_mutative_command(f"npx {js_rm_script}")
        assert result.is_mutative is True
        assert result.verb == "fs-delete"

    def test_unreadable_wrapped_script_keeps_conservative_default(self, tmp_path):
        result = detect_mutative_command(f"uv run {tmp_path / 'missing.py'}")
        assert result.is_mutative is True
        assert result.verb == "script-file-unreadable"

    def test_npx_package_command_classifies_by_its_verb(self):
        result = detect_mutative_command("npx prisma migrate deploy")
        assert result.is_mutative is True
        assert result.verb == "migrate"

    @pytest.mark.parametrize("command", [
        "uv run pytest -q",
        "uv run ruff check .",
        "poetry run pytest tests/",
        "pipx run cowsay hello",
        "npx tsc --noEmit",
        "npx eslint .",
    ])
    def test_benign_runner_payloads_stay_non_mutative(self, command):
        assert detect_mutative_command(command).is_mutative is False

    @pytest.mark.parametrize("command", ["uv pip list", "poetry show", "uv run", "npx"])
    def test_non_runner_forms_are_left_to_ordinary_detection(self, command):
        result = detect_mutative_command(command)
        assert "re-dispatched" not in result.reason
        assert result.is_mutative is False

    def test_uv_pip_install_keeps_its_mutative_verdict(self):
        result = detect_mutative_command("uv pip install requests")
        assert result.is_mutative is True
        assert result.verb == "install"


class TestPythonModuleWidening:
    """``python -m <module>`` must not classify lower than the direct CLI form."""

    def test_module_cli_with_mutative_verb_escalates(self):
        result = detect_mutative_command("python3 -m alembic upgrade head")
        assert result.is_mutative is True
        assert result.verb == "upgrade"

    def test_module_widening_matches_direct_form(self):
        via_module = detect_mutative_command("python3 -m alembic upgrade head")
        direct = detect_mutative_command("alembic upgrade head")
        assert via_module.is_mutative == direct.is_mutative
        assert via_module.verb == direct.verb

    @pytest.mark.parametrize("command", [
        "python3 -m pytest tests/",
        "python3 -m http.server 8000",
        "python3 -m json.tool /tmp/x.json",
        "python3 -m unittest discover -s tests",
    ])
    def test_benign_module_invocations_are_not_relabelled(self, command):
        # The widening is escalate-only: a non-mutative rewrite is discarded so
        # ordinary detection classifies the command untouched.
        result = detect_mutative_command(command)
        assert result.is_mutative is False
        assert "re-dispatched" not in result.reason

    def test_package_manager_path_is_unchanged(self):
        result = detect_mutative_command("python3 -m pip install requests")
        assert result.is_mutative is True
        assert result.reason == (
            "'python3 -m pip' re-dispatched as 'pip': Mutative verb 'install'"
        )


class TestStdinPayload:
    """An interpreter reading its program from stdin cannot be inspected."""

    def test_stdin_sentinel_is_conservative(self):
        # The negative proof: `python3 - < payload.py` passed as T0 while the
        # equivalent `cat payload.py | python3` was already gated.
        result = detect_mutative_command("python3 - < /tmp/payload.py")
        assert result.is_mutative is True
        assert result.verb == "script-stdin-payload"

    def test_heredoc_payload_is_still_analyzed_not_assumed(self):
        command = "python3 - <<'PYEOF'\nimport json\nprint(json.dumps({}))\nPYEOF"
        result = detect_mutative_command(command)
        assert result.is_mutative is False

    def test_heredoc_with_mutative_body_still_caught(self):
        command = "python3 - <<'PYEOF'\nimport os\nos.remove('/tmp/x')\nPYEOF"
        assert detect_mutative_command(command).is_mutative is True

    def test_inline_code_flag_is_not_treated_as_stdin(self):
        result = detect_mutative_command('python3 -c "import json" -')
        assert result.verb != "script-stdin-payload"

    def test_script_argument_named_dash_does_not_trigger(self, py_copy_script):
        # The `-` here is an argument to the script, not the program source.
        result = detect_mutative_command(f"python3 {py_copy_script} -")
        assert result.verb == "shutil-copy"


class TestJsDirectFilesystemMutation:
    """A JS mutation that never reaches an exec sink must still be seen."""

    def test_rm_sync_in_script_is_mutative(self, js_rm_script):
        # The negative proof: identical call, T3 under `node -e` and T0 here.
        result = detect_mutative_command(f"node {js_rm_script}")
        assert result.is_mutative is True
        assert result.verb == "fs-delete"

    def test_script_matches_inline_verdict_for_same_call(self, js_rm_script):
        script = detect_mutative_command(f"node {js_rm_script}")
        inline = detect_mutative_command(
            'node -e "require(\'fs\').rmSync(\'/tmp/t\')"'
        )
        assert script.is_mutative == inline.is_mutative is True

    @pytest.mark.parametrize("body,expected_verb", [
        ('const fs = require("fs");\nfs.writeFileSync("/tmp/o", "d");\n', "fs-write"),
        ('import fs from "node:fs";\nfs.renameSync("/tmp/a", "/tmp/b");\n', "fs-mutate"),
        ('const fs = require("fs");\nfs.chmodSync("/tmp/a", 0o777);\n', "fs-chmod"),
        ('const fsp = require("fs").promises;\nawait fsp.unlink("/tmp/a");\n', "fs-delete"),
        ('const fs = require("fs");\nawait fs.promises.rm("/tmp/a");\n', "fs-delete"),
        ('const { rmSync } = require("node:fs");\nrmSync("/tmp/a");\n', "fs-delete"),
        ('require("fs").unlinkSync("/tmp/a");\n', "fs-delete"),
    ], ids=[
        "write-sync", "rename-sync", "chmod-sync", "promises-alias",
        "promises-chain", "destructured-sync", "inline-require",
    ])
    def test_direct_mutation_forms(self, tmp_path, body, expected_verb):
        script = tmp_path / "m.mjs"
        script.write_text(body)
        result = detect_mutative_command(f"node {script}")
        assert result.is_mutative is True
        assert result.verb == expected_verb

    def test_mutation_named_only_in_a_comment_is_not_a_call(self, tmp_path):
        script = tmp_path / "doc.js"
        script.write_text(
            "// fs.rmSync(target) used to live here\n"
            "/* fs.writeFileSync(out, data) is described above */\n"
            "console.log('report');\n"
        )
        assert detect_mutative_command(f"node {script}").is_mutative is False

    def test_mutation_named_only_inside_a_string_is_not_a_call(self, tmp_path):
        script = tmp_path / "hint.js"
        script.write_text(
            'const hint = "call fs.rmSync(path) to clean up";\n'
            "const tmpl = `then fs.writeFileSync(out, data)`;\n"
            "console.log(hint, tmpl);\n"
        )
        assert detect_mutative_command(f"node {script}").is_mutative is False

    def test_read_only_script_stays_non_mutative(self, tmp_path):
        script = tmp_path / "read.js"
        script.write_text(
            'const fs = require("fs");\n'
            'const raw = fs.readFileSync("/tmp/in.json", "utf8");\n'
            "const set = new Set(Object.keys(JSON.parse(raw)));\n"
            "process.stdout.write(`${set.size}\\n`);\n"
        )
        assert detect_mutative_command(f"node {script}").is_mutative is False

    def test_directory_creation_stays_aligned_with_the_mkdir_override(self, tmp_path):
        # `mkdir` into the working tree is deliberately T0 in the shell lane, so
        # fs.mkdirSync is deliberately absent from the direct-mutation set.
        script = tmp_path / "mk.js"
        script.write_text('const fs = require("fs");\nfs.mkdirSync("./build");\n')
        assert detect_mutative_command(f"node {script}").is_mutative is False

    def test_non_fs_receiver_is_not_matched(self, tmp_path):
        script = tmp_path / "other.js"
        script.write_text(
            "const prefs = makeStore();\n"
            "prefs.write(payload);\n"
            "queue.rename(next);\n"
        )
        assert detect_mutative_command(f"node {script}").is_mutative is False

    def test_exec_sink_detection_still_works(self, tmp_path):
        script = tmp_path / "sink.js"
        script.write_text(
            'const { execSync } = require("child_process");\n'
            'execSync("kubectl delete deployment foo");\n'
        )
        assert detect_mutative_command(f"node {script}").is_mutative is True

    def test_benign_exec_sink_is_not_escalated(self, tmp_path):
        script = tmp_path / "ls.js"
        script.write_text(
            'const { execSync } = require("child_process");\n'
            'execSync("ls -la");\n'
        )
        assert detect_mutative_command(f"node {script}").is_mutative is False
