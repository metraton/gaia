"""Code whose effect or target the parse cannot prove asks, in every lane that runs it."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import sensitive_read_guard  # noqa: E402
from modules.security.blocked_commands import is_blocked_command  # noqa: E402
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402

LONG_PAD = "\n".join(f"x{i} = {i}" for i in range(80))

PY_ASKS = {
    "zip-writestr": "import zipfile\nzipfile.ZipFile('z.zip', 'w').writestr('a', 'b')",
    "zip-append": "import zipfile\nz = zipfile.ZipFile('z.zip', mode='a')\nz.write('f')",
    "tar-write": "import tarfile\ntarfile.open('x.tar.gz', 'w:gz').add('src')",
    "gzip-write": "import gzip\ngzip.open('x.gz', 'wb').close()",
    "bz2-write": "import bz2\nbz2.open('x.bz2', 'xb').close()",
    "lzma-mode-var": "import lzma\nm = 'w'\nlzma.open('x.xz', m).close()",
    "environ-assign": "import os, subprocess\nos.environ['GIT_DIR'] = '/x'\nsubprocess.run(['git', 'status'])",
    "environ-update": "import os, subprocess\nos.environ.update(GIT_DIR='/x')\nsubprocess.run(['git', 'status'])",
    "environ-setdefault": "import os, subprocess\nos.environ.setdefault('GIT_DIR', '/x')\nsubprocess.run(['git', 'status'])",
    "putenv": "import os, subprocess\nos.putenv('GIT_DIR', '/x')\nsubprocess.check_output(['git', 'status'])",
    "class-dunder": "().__class__.__base__.__subclasses__()",
    "func-globals": "print((lambda: 0).__globals__)",
    "b64-path": "import base64\nprint(open(base64.b64decode('fi8uc3NoL2lkX3JzYQ==').decode()).read())",
    "chr-path": "print(open(''.join(map(chr, [46, 101, 110, 118]))).read())",
    "reversed-path": "import os\nprint(open(os.path.expanduser('asr_di/hss./~'[::-1])).read())",
    "concat-path": "print(open('.e' + 'nv').read())",
    "fstring-path": "h = input()\nprint(open(f'{h}/x').read())",
    "input-path": "from pathlib import Path\nprint(Path(input()).read_text())",
    "io-open-computed": "import io\nprint(io.open(''.join(['.', 'env'])).read())",
    "os-open-computed": "import os\nos.read(os.open('.e' + 'nv', os.O_RDONLY), 99)",
    "shutil-computed": "import shutil\nshutil.copy('a' + 'b', 'c')",
    "listdir-computed": "import os\nprint(os.listdir('~/.s' + 'sh'))",
    "listdir-environ": "import os\nprint(os.listdir(os.environ['HOME']))",
    "scandir-getenv": "import os\nprint(list(os.scandir(os.getenv('HOME'))))",
    "walk-home": "import os\nfrom pathlib import Path\nprint(next(os.walk(Path.home())))",
    "iterdir-home": "from pathlib import Path\nprint(list(Path.home().iterdir()))",
    "git-c-pager": "import subprocess\nsubprocess.run(['git', '-c', 'core.pager=sh -c x', 'log'])",
    "git-c-pager-read": "import subprocess\nsubprocess.run(['git', '-c', 'core.pager=less', 'log'])",
    "git-c-alias": "import subprocess\nsubprocess.run(['git', '-c', 'alias.x=!id', 'x'])",
    "git-c-credential": "import subprocess\nsubprocess.run(['git', '-c', 'credential.helper=store', 'fetch'])",
    "git-exec-path": "import subprocess\nsubprocess.run(['git', '--exec-path=/tmp/x', 'status'])",
}
PY_FREE = {
    "json": "import json\nd = json.loads('{\"a\": 1}')\nprint(d['a'])",
    "tempdir": "import tempfile\nprint(tempfile.gettempdir())",
    "hostname": "import socket\nprint(socket.gethostname())",
    "sqlite-select": "import sqlite3\nc = sqlite3.connect(':memory:')\nprint(c.execute('select 1').fetchall())",
    "git-status-argv": "import subprocess\nprint(subprocess.run(['git', 'status'], capture_output=True, text=True).stdout)",
    "environ-read-then-run": "import os, subprocess\nprint(os.environ.get('TMPDIR'))\nsubprocess.run(['git', 'status'])",
    "open-literal": "print(open('README.md').read())",
    "path-literal": "from pathlib import Path\nprint(Path('README.md').read_text())",
    "bound-literal": "p = 'README.md'\nprint(open(p).read())",
    "joined-literals": "import os\nprint(open(os.path.join('docs', 'README.md')).read())",
    "zip-read": "import zipfile\nprint(zipfile.ZipFile('z.zip').namelist())",
    "tar-read": "import tarfile\nprint(tarfile.open('x.tar.gz', 'r:gz').getnames())",
    "listdir-literal": "import os\nprint(os.listdir('docs'))",
    "listdir-cwd": "import os\nprint(sorted(os.listdir(os.getcwd())))",
    "git-c-data-key": "import subprocess\nsubprocess.run(['git', '-c', 'color.ui=never', 'status'])",
}
PY_SECRETS = {
    "ssh": "print(open('/home/u/.ssh/id_rsa').read())",
    "env": "print(open('.env').read())",
    "aws-join": "from pathlib import Path\nprint(Path.home().joinpath('.aws', 'credentials').read_text())",
}


def _verdict(command: str, cwd: str) -> str:
    if is_blocked_command(command).is_blocked or not sensitive_read_guard.check(command, cwd, program=command)[0]:
        return "BLOCKED"
    return "ASK" if detect_mutative_command(command, cwd=cwd).is_mutative else "FREE"


def _python_spellings(code: str, tmp_path: Path) -> dict:
    (tmp_path / "s.py").write_text(code + "\n", encoding="utf-8")
    long_code = LONG_PAD + "\n" + code
    return {
        "-c": f"python3 -c {shlex.quote(code.replace(chr(10), '; '))}",
        "-c long": f"python3 -c {shlex.quote(long_code.replace(chr(10), '; '))}",
        "heredoc": f"python3 - <<'PY'\n{code}\nPY",
        "file": f"python3 {tmp_path / 's.py'}",
        "file via cd": f"cd {tmp_path} && python3 s.py",
    }


@pytest.mark.parametrize("name", list(PY_ASKS))
def test_python_effect_or_unprovable_target_asks_in_every_lane(name, tmp_path):
    for lane, command in _python_spellings(PY_ASKS[name], tmp_path).items():
        assert _verdict(command, str(tmp_path)) != "FREE", (name, lane)


@pytest.mark.parametrize("name", list(PY_FREE))
def test_python_pure_read_runs_free_in_every_lane(name, tmp_path):
    for lane, command in _python_spellings(PY_FREE[name], tmp_path).items():
        assert _verdict(command, str(tmp_path)) == "FREE", (name, lane)


@pytest.mark.parametrize("name", list(PY_SECRETS))
def test_python_literal_secret_read_stays_blocked(name, tmp_path):
    for lane, command in _python_spellings(PY_SECRETS[name], tmp_path).items():
        assert _verdict(command, str(tmp_path)) == "BLOCKED", (name, lane)


def test_a_python_script_running_a_code_string_asks_while_inline_judges_the_literal(tmp_path):
    code = "print(eval('1 + 1'))"
    spellings = _python_spellings(code, tmp_path)
    assert _verdict(spellings["-c"], str(tmp_path)) == "FREE"
    assert _verdict(spellings["file"], str(tmp_path)) == "ASK"
    (tmp_path / "x.py").write_text("exec(\"import os\nos.remove('a')\")\n", encoding="utf-8")
    assert _verdict(f"python3 {tmp_path / 'x.py'}", str(tmp_path)) == "ASK"


def test_a_python_script_that_does_not_parse_is_unknown_and_asks(tmp_path):
    (tmp_path / "broken.py").write_text("print 'hello'\n", encoding="utf-8")
    assert _verdict(f"python3 {tmp_path / 'broken.py'}", str(tmp_path)) == "ASK"


PY_BLOCK_READ = "import json\ndata = json.loads('{\"a\": 1}')\nfor key in sorted(data):\n    print(key, data[key])"
PY_BLOCK_EFFECT = "import shutil\nfor path in ['build']:\n    shutil.rmtree(path)"


def _multiline_c_spellings(code: str) -> dict:
    long_code = LONG_PAD + "\n" + code
    ansi_c = long_code.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    return {
        "single-quoted": f"python3 -c {shlex.quote(long_code)}",
        "double-quoted": 'python3 -c "' + long_code.replace('"', '\\"') + '"',
        "ansi-c": f"python3 -c $'{ansi_c}'",
        "cat heredoc": f"python3 -c \"$(cat <<'PY'\n{long_code}\nPY\n)\"",
        "shift operator": f"python3 -c {shlex.quote('x = 1 << 2' + chr(10) + long_code)}",
    }


@pytest.mark.parametrize("spelling", list(_multiline_c_spellings(PY_BLOCK_READ)))
def test_a_multiline_python_c_payload_is_parsed_as_the_shell_passes_it(spelling, tmp_path):
    assert _verdict(_multiline_c_spellings(PY_BLOCK_READ)[spelling], str(tmp_path)) == "FREE"
    assert _verdict(_multiline_c_spellings(PY_BLOCK_EFFECT)[spelling], str(tmp_path)) == "ASK"


def test_a_heredoc_after_a_c_program_is_its_stdin_not_its_program(tmp_path):
    command = "python3 -c 'import shutil; shutil.rmtree(\"build\")' <<'D'\nprint(1)\nD"
    assert _verdict(command, str(tmp_path)) == "ASK"


@pytest.mark.parametrize("code", ["print 'hello'", LONG_PAD + "\nprint 'hello'", "def f(:\n    pass"])
def test_inline_python_that_does_not_parse_is_unknown_and_asks(code, tmp_path):
    result = detect_mutative_command(f"python3 -c {shlex.quote(code)}", cwd=str(tmp_path))
    assert result.is_mutative and result.category == "UNKNOWN", result


JS_ASKS = {
    "fs-write": "require('fs').writeFileSync('o.txt', 'x')",
    "fs-rm": "require('fs').rmSync('build', {recursive: true})",
    "fs-bracket": "require('fs')['unlink' + 'Sync']('a')",
    "fs-bound-bracket": "const f = require('fs'); f[process.argv[1]]('a')",
    "net": "require('net').connect(80, 'example.com')",
    "net-import": "import('node:net').then(n => n.connect(80))",
    "fetch-post": "fetch('https://example.com', {method: 'POST', body: 'x'})",
    "buffer-eval": "eval(Buffer.from('cmVxdWlyZSgiZnMiKS51bmxpbmtTeW5jKCJhIik=', 'base64').toString())",
    "destructured": "const { writeFileSync } = require('fs'); writeFileSync('o.txt', 'x')",
    "destructured-rename": "const { rmSync: z } = require('fs'); z('build', {recursive: true})",
    "destructured-promises": "const { rm } = require('fs/promises'); rm('build', {recursive: true})",
    "promises-member": "require('fs/promises').rm('build', {recursive: true})",
    "fs-alias": "const f = require('fs'); f.writeFileSync('o', 'x')",
    "fs-passed": "const f = require('node:fs'); helper(f)",
    "import-named": "import { unlinkSync } from 'fs'; unlinkSync('a')",
    "cp-renamed": "const { execSync: run } = require('child_process'); run('rm -rf x')",
    "env-spawn": "process.env.GIT_DIR = '/x'; require('child_process').execFileSync('git', ['status'])",
    "optchain-require": "require?.('fs').unlinkSync('a')",
    "create-require": "require('module').createRequire(__filename)('fs').unlinkSync('a')",
    "spawn-sh": "require('child_process').spawn('sh', ['-c', 'rm -rf x'])",
    "execfile-bash": "require('child_process').execFileSync('/bin/bash', ['-c', 'git push --force'])",
    "spawn-cmd": "require('child_process').spawnSync('cmd', ['/c', 'del /q build'])",
    "spawn-sh-computed": "const c = process.argv[2]; require('child_process').spawnSync('sh', ['-c', c])",
    "deno-remove": "Deno.removeSync('a')",
    "deno-write": "Deno.writeTextFileSync('o.txt', 'x')",
    "deno-command": "new Deno.Command('rm', {args: ['-rf', 'x']}).outputSync()",
    "deno-connect": "Deno.connect({port: 80})",
    "bun-write": "Bun.write('o.txt', 'x')",
    "bun-spawn": "Bun.spawnSync(['rm', '-rf', 'x'])",
    "bun-shell": "Bun.$`rm -rf x`",
    "bun-import-shell": "import { $ } from 'bun'; await $`rm -rf x`",
}
JS_FREE = {
    "json": "console.log(JSON.parse('{\"a\":1}').a)",
    "hostname": "console.log(require('os').hostname())",
    "fs-literal-member": "const fs = require('fs'); console.log(fs.readFileSync('package.json', 'utf8'))",
    "fs-destructured-read": "const { readFileSync, existsSync: has } = require('fs'); console.log(has('a') && readFileSync('a', 'utf8'))",
    "promises-read": "require('fs/promises').readFile('package.json', 'utf8').then(console.log)",
    "deno-read": "console.log(Deno.readTextFileSync('package.json'), Deno.cwd(), Deno.args)",
    "bun-read": "console.log(Bun.version, Bun.argv, Bun.env.HOME)",
}
JS_FILE_FREE = {
    "fetch-get": "fetch('https://example.com').then(r => r.text()).then(console.log)",
    "stdout": "process.stdout.write(String(require('os').cpus().length))",
    "mention": "// eval(x) and require('net') appear only in this comment\nconsole.log('import(x)')",
    "spawn-sh-read": "require('child_process').spawnSync('sh', ['-c', 'git status'], {stdio: 'inherit'})",
    "execfile-read": "require('child_process').execFileSync('git', ['status'])",
}


def _js_spellings(code: str, tmp_path: Path) -> dict:
    (tmp_path / "s.js").write_text(code + "\n", encoding="utf-8")
    (tmp_path / "s.mjs").write_text(code + "\n", encoding="utf-8")
    return {
        "-e": f"node -e {shlex.quote(code)}",
        "-p": f"node -p {shlex.quote(code)}",
        "--print": f"node --print {shlex.quote(code)}",
        "heredoc": f"node - <<'JS'\n{code}\nJS",
        "file": f"node {tmp_path / 's.js'}",
        "mjs": f"node {tmp_path / 's.mjs'}",
        "deno eval": f"deno eval {shlex.quote(code)}",
        "deno run": f"deno run -A {tmp_path / 's.js'}",
        "deno file": f"deno {tmp_path / 's.js'}",
        "bun -e": f"bun -e {shlex.quote(code)}",
        "bun file": f"bun {tmp_path / 's.js'}",
        "bun run": f"bun run {tmp_path / 's.js'}",
    }


@pytest.mark.parametrize("name", list(JS_ASKS))
def test_javascript_effect_asks_in_every_lane(name, tmp_path):
    for lane, command in _js_spellings(JS_ASKS[name], tmp_path).items():
        assert _verdict(command, str(tmp_path)) != "FREE", (name, lane)


@pytest.mark.parametrize("name", list(JS_FREE))
def test_javascript_read_runs_free_in_every_lane(name, tmp_path):
    for lane, command in _js_spellings(JS_FREE[name], tmp_path).items():
        assert _verdict(command, str(tmp_path)) == "FREE", (name, lane)


@pytest.mark.parametrize("name", list(JS_FILE_FREE))
def test_javascript_file_screen_reads_code_not_comments_or_strings(name, tmp_path):
    spellings = _js_spellings(JS_FILE_FREE[name], tmp_path)
    for lane in ("file", "mjs", "deno run", "bun file"):
        assert _verdict(spellings[lane], str(tmp_path)) == "FREE", (name, lane)


def test_bun_and_deno_forms_that_name_no_script_file_keep_their_own_lanes(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"build": "tsc"}}', encoding="utf-8")
    assert _verdict(f"bun run --cwd {tmp_path} build", str(tmp_path)) == "FREE"
    assert _verdict("deno fmt --check", str(tmp_path)) == "FREE"
    assert _verdict(f"deno run -A {tmp_path / 'missing.ts'}", str(tmp_path)) == "ASK"
    assert _verdict("deno run https://example.com/x.ts", str(tmp_path)) == "ASK"


UNPARSED = {
    "ruby": ("-e", "rb", {
        "asks": ["File.send(:delete, 'a')", "File.write('o.txt', 'x')", "puts \"#{File.read('a')}\"", "puts x"],
        "free": ["puts 1 + 1", "puts 'hello'", "p(2 * 3); puts \"done\\n\""],
    }),
    "perl": ("-e", "pl", {
        "asks": ["unlink 'a'", "open(my $f, '>', 'o.txt'); print $f 'x'", "print \"$ENV{HOME}\""],
        "free": ["print 1+1", "print \"hello\\n\"", "say 'x'"],
    }),
    "php": ("-r", "php", {
        "asks": ["file_put_contents('o.txt', 'x');", "shell_exec('rm -rf build');", "echo \"$x\";"],
        "free": ["echo 1+1;", "echo 'hi', \"\\n\";", "print('x');"],
    }),
    "lua": ("-e", "lua", {
        "asks": ["os.remove('a')", "os.execute('rm -rf x')", "io.open('o.txt', 'w'):write('x')", "io.popen('ls')"],
        "free": ["print(1)", "print('hello')"],
    }),
    "luajit": ("-e", "lua", {
        "asks": ["loadstring(\"os.remove('a')\")()", "dofile('evil.lua')", "print(os.execute('rm -rf x'))"],
        "free": ["print(1)", "print('hello')"],
    }),
    "Rscript": ("-e", "R", {
        "asks": ["file.remove('a')", "system('rm -rf x')", "unlink('b', recursive=TRUE)", "writeLines('x', 'o.txt')"],
        "free": ["print(1)", "print('hello')"],
    }),
}


@pytest.mark.parametrize("language", list(UNPARSED))
def test_inline_code_with_no_parser_asks_unless_it_only_prints_literals(language, tmp_path):
    flag, extension, cases = UNPARSED[language]
    for verdict, codes in (("ASK", cases["asks"]), ("FREE", cases["free"])):
        for index, code in enumerate(codes):
            script = tmp_path / f"{verdict}{index}.{extension}"
            script.write_text(("<?php " if language == "php" else "") + code + "\n", encoding="utf-8")
            for command in (f"{language} {flag} {shlex.quote(code)}", f"{language} {script}"):
                actual = _verdict(command, str(tmp_path))
                assert (actual == "FREE") == (verdict == "FREE"), (verdict, code, command.split()[1],
                                                                   detect_mutative_command(command).reason)


@pytest.mark.parametrize("spelling", ["R -e {code}", "R --vanilla -e {code}", "R --no-save -e {code}", "R CMD BATCH {script}"])
def test_bare_r_is_judged_like_rscript(spelling, tmp_path):
    for verdict, code in (("ASK", "file.remove('a')"), ("ASK", "system('rm -rf x')"), ("FREE", "print('hello')")):
        script = tmp_path / f"{verdict}.R"
        script.write_text(code + "\n", encoding="utf-8")
        command = spelling.format(code=shlex.quote(code), script=script)
        assert _verdict(command, str(tmp_path)) == verdict, (command, detect_mutative_command(command).reason)
