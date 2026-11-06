"""The declaration channel is signed to write and refused to a subagent; Python that reaches the store is refused (AC-12)."""

import os
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[4]
for _p in (_ROOT, _ROOT / "hooks", _ROOT / "bin"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from modules.security.gaia_cli_only_guard import ALLOWED_WRITE_PHRASES  # noqa: E402
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.security.subagent_memory_write_guard import DECLARATION_REJECTION, check  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

DECLARE = "gaia memory declare --project proj --command 'make build' --effect local"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    for key in ("GAIA_DB", "GAIA_DISPATCH_AGENT"):
        monkeypatch.delenv(key, raising=False)
    from gaia.paths import db_path
    from gaia.store.writer import _connect

    db = db_path()
    _connect(db).close()
    project, other = tmp_path / "proj", tmp_path / "other"
    (project / "sub").mkdir(parents=True)
    other.mkdir()
    con = sqlite3.connect(db)
    for name, path in (("proj", project), ("other", other)):
        con.execute("INSERT INTO projects (workspace, name, path, status) VALUES ('me', ?, ?, 'active')",
                    (name, str(path)))
    for binary in ("frobctl", "rm"):
        con.execute(
            "INSERT INTO memory (workspace, name, type, description, body, kind, owner_level, binary) "
            "VALUES ('me', ?, 'user', ?, ?, 'tool', 'user', ?)",
            (f"user_tool_{binary}", binary, binary, binary))
    con.commit()
    con.close()
    yield project, other


@pytest.mark.parametrize("command", [
    DECLARE,
    "gaia --json memory declare --binary frobctl --effect read",
    "gaia memory declare --binary frobctl --withdraw",
    "/opt/elsewhere/bin/gaia memory declare --binary frobctl --effect read",
])
def test_the_write_verb_asks_for_a_signature_in_every_spelling(command):
    assert detect_mutative_command(command).is_mutative
    assert ("memory", "declare") in ALLOWED_WRITE_PHRASES


@pytest.mark.parametrize("agent", ["developer", "gaia-operator", "gaia-system"])
def test_a_subagent_is_refused_and_the_orchestrator_passes_to_the_gate(agent):
    assert check(DECLARE, is_subagent=True, agent_type=agent) == (False, DECLARATION_REJECTION)
    assert check(f"/x/bin/gaia {DECLARE[5:]}", is_subagent=True, agent_type=agent)[0] is False
    assert check(DECLARE, is_subagent=False) == (True, None)


def test_the_writer_refuses_a_dispatched_caller(store, monkeypatch):
    from gaia.store.declarations import DeclarationRefused, declare_command, declare_tool_effect

    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "a1")
    with pytest.raises(DeclarationRefused):
        declare_command("me", "proj", "make build", "local")
    with pytest.raises(DeclarationRefused):
        declare_tool_effect("frobctl", "read")


@pytest.mark.parametrize("form", [
    "make $T", "make 'build'", "make b*", "make {a,b}", "A=1 make build", "make build > x",
    "make build && rm x", "~/make build", "make `x`", "-x build",
])
def test_only_a_literal_form_can_be_declared(store, form):
    from gaia.store.declarations import DeclarationRefused, declare_command

    with pytest.raises(DeclarationRefused):
        declare_command("me", "proj", form, "local")


_STORE_CODE = {
    "approve": "python3 -c 'from gaia.approvals.store import approve; approve(\"P-x\")'",
    "declare": "python3 -c 'from gaia.store.declarations import declare_command; declare_command(\"me\", \"p\", \"make build\", \"read\")'",
    "package": "python3 -I -c 'import gaia.contract'",
    "dynamic": "python3 -c 'import importlib; importlib.import_module(\"gaia.approvals\")'",
    "module": "python3 -m gaia.store.writer",
    "bundled-flags": "python3 -Ic 'from gaia.approvals import store'",
    "local-module": "python3 -c 'import mymod'",
    "local-module-m": "python3 -m mymod",
    "script-imports-local": "python3 {cwd}/run.py",
    "symlinked-package": "python3 -c 'import g.approvals.store as s'",
    "uv-run": "uv run python3 -c 'from gaia.approvals import store'",
    "shebang-noext": "{cwd}/tool",
    "ipython": "ipython -c 'from gaia.approvals import store'",
    "heredoc": "python3 - <<'PY'\nfrom gaia.store import writer\nPY",
    "store-literal": "python3 -c 'import sqlite3; sqlite3.connect(\"{store}\").execute(\"DELETE FROM memory\")'",
    "store-uri": "python3 -c 'import sqlite3; sqlite3.connect(\"file:{store}?mode=rw\", uri=True)'",
    "store-env": "python3 -c 'import os, sqlite3; sqlite3.connect(os.environ[\"GAIA_DB\"]).execute(\"DELETE FROM memory\")'",
    "aliased": "python3 -c 'from sqlite3 import connect as c; c(\"{store}\")'",
    "exec-literal": "python3 -c \"exec('from gaia.approvals import store')\"",
    "package-main": "python3 -m mypkg",
    "shebang-renamed-interpreter": "{cwd}/snaketool",
    "warnings-option": "python3 -W ignore::gaia.approvals.store.Warning -c 'print(1)'",
    "warnings-env": "PYTHONWARNINGS=ignore::gaia.approvals.store.Warning python3 -c 'print(1)'",
    "script": "python3 {script}",
}


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    store = tmp_path / "isolated" / "gaia.db"
    monkeypatch.setenv("GAIA_DB", str(store))
    approve = "from gaia.approvals import store\nstore.approve('P-x')\n"
    script = tmp_path / "write.py"
    script.write_text(approve)
    (tmp_path / "mymod.py").write_text(approve)
    (tmp_path / "run.py").write_text("import mymod\n")
    (tmp_path / "helper.py").write_text("import json\nVALUE = json.dumps(1)\n")
    (tmp_path / "clean.py").write_text("import helper\nprint(helper.VALUE)\n")
    (tmp_path / "snake").symlink_to(os.path.realpath(sys.executable))
    for name, interpreter in (("snaketool", tmp_path / "snake"), ("frobtool", "/usr/bin/env frobinterp")):
        (tmp_path / name).write_text(f"#!{interpreter}\n" + approve)
        (tmp_path / name).chmod(0o755)
    (tmp_path / "mypkg").mkdir()
    (tmp_path / "mypkg" / "__init__.py").write_text("")
    (tmp_path / "mypkg" / "__main__.py").write_text(approve)
    (tmp_path / "u7.py").write_bytes(
        b"# -*- coding: utf-7 -*-\n#+AAo-from gaia.approvals import store+AAo-store.approve('P-x')\n")
    tool = tmp_path / "tool"
    tool.write_text("#!/usr/bin/env python3\n" + approve)
    tool.chmod(0o755)
    (tmp_path / "g").symlink_to(_ROOT / "gaia")
    (tmp_path / "alias").mkdir()
    (tmp_path / "alias" / "gx").symlink_to(_ROOT / "gaia")
    return store, script, tmp_path


@pytest.mark.parametrize("subagent", [True, False])
@pytest.mark.parametrize("case", sorted(_STORE_CODE))
def test_python_that_reaches_the_store_is_refused_like_a_direct_write(isolated_store, case, subagent):
    store, script, cwd = isolated_store
    command = _STORE_CODE[case].format(store=store, script=script, cwd=cwd)
    result = BashValidator().validate(command, is_subagent=subagent, agent_type="developer", cwd=str(cwd))
    assert (result.allowed, result.rule, result.approval_id) == (False, "floor.gaia-db-write", None)


def test_a_test_run_never_writes_the_real_data_directory(tmp_path, monkeypatch):
    from gaia.store import writer

    account = tmp_path / "account"
    real_db = account / ".gaia" / "gaia.db"
    monkeypatch.setattr(writer, "_account_home", lambda: str(tmp_path / "elsewhere"), raising=False)
    writer._connect(real_db).close()
    monkeypatch.setattr(writer, "_account_home", lambda: str(account), raising=False)
    con = writer._connect(real_db)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            con.execute("INSERT INTO schema_version (version, applied_at) VALUES (999, 'x')")
        assert con.execute("SELECT COUNT(*) FROM schema_version WHERE version = 999").fetchone()[0] == 0
    finally:
        con.close()
    with pytest.raises(sqlite3.DatabaseError):
        writer.assert_schema_writable(real_db)
    linked = tmp_path / "linked.db"
    linked.symlink_to(real_db)
    with pytest.raises(sqlite3.DatabaseError):
        writer.assert_schema_writable(linked)
    temporary = tmp_path / "temporary" / "gaia.db"
    con = writer._connect(temporary)
    con.execute("INSERT INTO schema_version (version, applied_at) VALUES (999, 'x')")
    con.close()


_UNPROVEN_CODE = {
    "computed-import": "python3 -c 'import importlib, sys; importlib.import_module(sys.argv[1])' gaia.store",
    "concat-import": "python3 -c \"import importlib; importlib.import_module('ga'+'ia.store')\"",
    "dunder-import-alias": "python3 -c \"imp = __import__; imp('gaia.approvals.store')\"",
    "import-module-alias": "python3 -c \"from importlib import import_module as im; im('x')\"",
    "getattr-importlib": "python3 -c \"import importlib; getattr(importlib, 'import_module')('x')\"",
    "runpy-run-path": "python3 -c \"import runpy; runpy.run_path('write.py')\"",
    "runpy-alias": "python3 -c \"from runpy import run_module as r; r('mymod')\"",
    "exec-string": "python3 -c \"exec(input())\"",
    "exec-compile": "python3 -c \"exec(compile(open('write.py').read(), 'x', 'exec'))\"",
    "exec-alias": "python3 -c \"run = exec; run('1')\"",
    "builtins": "python3 -c \"import builtins; builtins.exec('1')\"",
    "dunder-chain": "python3 -c \"().__class__.__base__.__subclasses__()\"",
    "syspath-alias": "python3 -c \"import sys; sys.path.insert(0, '{cwd}/alias'); import gx.approvals.store\"",
    "pythonpath-alias": "PYTHONPATH={cwd}/alias python3 -c 'import gx.approvals.store'",
    "stdin-redirect": "python3 < {cwd}/write.py",
    "pipe-into-python": "cat {cwd}/write.py | python3",
    "uv-run-script": "uv run python3 {cwd}/write.py",
    "timeout-wrapper": "timeout 5 python3 -c 'from gaia.approvals import store'",
    "unparsed": "python3 -c 'import (('",
    "utf7-cookie": "python3 {cwd}/u7.py",
    "unknown-shebang": "{cwd}/frobtool",
    "unresolved-module": "python3 -c 'import evil_user_site_module'",
    "unmodelled-option": "python3 -X importtime -c 'print(1)'",
}


@pytest.mark.parametrize("subagent", [True, False])
@pytest.mark.parametrize("case", sorted(_UNPROVEN_CODE))
def test_python_whose_closure_is_not_proven_never_runs_free(isolated_store, case, subagent):
    cwd = isolated_store[2]
    command = _UNPROVEN_CODE[case].format(cwd=cwd)
    result = BashValidator().validate(command, is_subagent=subagent, agent_type="developer", cwd=str(cwd))
    assert not result.allowed, (case, result.tier, result.rule)


@pytest.mark.parametrize("command", [
    "python3 -c 'import json; print(json.dumps(1))'",
    "python3 -c 'import sqlite3; sqlite3.connect(\":memory:\")'",
    "python3 --version",
    "python3 -c 'import helper; print(helper.VALUE)'",
    "python3 {cwd}/clean.py",
    "gaia memory list",
    f"python3 {_ROOT / 'bin' / 'gaia'} memory list",
])
def test_code_that_does_not_reach_the_store_keeps_its_lane(isolated_store, command):
    cwd = isolated_store[2]
    result = BashValidator().validate(command.format(cwd=cwd), is_subagent=True, agent_type="developer", cwd=str(cwd))
    assert result.rule != "floor.gaia-db-write"
    if command.startswith("python3") and "bin" not in command:
        assert result.allowed, (command, result.rule)
