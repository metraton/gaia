"""The declaration channel: signed to write, refused to a subagent, exact to match, never below a floor (AC-12)."""

import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[4]
for _p in (_ROOT, _ROOT / "hooks", _ROOT / "bin"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from modules.security import declared_effects  # noqa: E402
from modules.security.declared_effects import DECLARED_EFFECTS, apply_declarations, declared_effect  # noqa: E402
from modules.security.effect_lattice import STORE, TOP, Effect, FS_WRITE, lattice_verdict  # noqa: E402
from modules.security.gaia_cli_only_guard import ALLOWED_WRITE_PHRASES  # noqa: E402
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.security.subagent_memory_write_guard import DECLARATION_REJECTION, check  # noqa: E402
from modules.security.verdict import Verdict  # noqa: E402
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
    declared_effects.reset_cache()
    yield project, other
    declared_effects.reset_cache()


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

    project, _ = store
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "a1")
    with pytest.raises(DeclarationRefused):
        declare_command("me", "proj", "make build", "local")
    with pytest.raises(DeclarationRefused):
        declare_tool_effect("frobctl", "read")
    assert declared_effect("make build", str(project)) is None


@pytest.mark.parametrize("form", [
    "make $T", "make 'build'", "make b*", "make {a,b}", "A=1 make build", "make build > x",
    "make build && rm x", "~/make build", "make `x`", "-x build",
])
def test_only_a_literal_form_can_be_declared(store, form):
    from gaia.store.declarations import DeclarationRefused, declare_command

    with pytest.raises(DeclarationRefused):
        declare_command("me", "proj", form, "local")


def test_a_form_matches_word_for_word_in_its_own_project_only(store):
    from gaia.store.declarations import declare_command

    project, other = store
    declare_command("me", "proj", "make build", "local")
    local = frozenset({DECLARED_EFFECTS["local"]})
    assert declared_effect("make build", str(project / "sub")) == local
    assert declared_effect("make   build", str(project)) == local
    for command in ("make deploy", "make build extra", "make", "make build;"):
        assert declared_effect(command, str(project)) is None
    assert declared_effect("make build", str(other)) is None
    assert declared_effect("make build", None) is None
    effects = apply_declarations(detect_mutative_command("make build", cwd=str(project)).effects,
                                 "make build", str(project))
    assert effects == local and lattice_verdict(effects, Verdict.UNKNOWN) is Verdict.FREE


def test_a_tool_is_judged_by_its_declared_effect(store):
    from gaia.store.declarations import declare_tool_effect

    _, other = store
    declare_tool_effect("frobctl", "read")
    declared_effects.reset_cache()
    assert declared_effect("frobctl list x", str(other)) == frozenset({DECLARED_EFFECTS["read"]})


def test_a_declaration_never_lowers_a_floor_a_guard_or_a_modelled_destroyer(store):
    from gaia.store.declarations import declare_command, declare_tool_effect

    project, _ = store
    for form in ("rm -rf build", "dd if=/dev/zero of=/dev/sda", "frobctl rm -rf /"):
        declare_command("me", "proj", form, "read")
    declare_tool_effect("frobctl", "read")
    declare_tool_effect("rm", "read")
    declared_effects.reset_cache()
    for form in ("dd if=/dev/zero of=/dev/sda", "frobctl rm -rf /"):
        assert declared_effect(form, str(project)) is None
    assert declared_effect("rm -rf build", str(project)) is not None
    for form in ("rm -rf build", "rm -rf build/x", "dd if=/dev/zero of=/dev/sda", "frobctl rm -rf /"):
        before = detect_mutative_command(form, cwd=str(project)).effects
        after = apply_declarations(before, form, str(project))
        assert lattice_verdict(after) is lattice_verdict(before)
    assert lattice_verdict(apply_declarations(
        detect_mutative_command("rm -rf build").effects, "rm -rf build", str(project))) is Verdict.SIGNED
    floored = TOP | {Effect(FS_WRITE, scope=STORE)}
    assert lattice_verdict(apply_declarations(floored, "frobctl list", str(project))) is Verdict.BLOCKED


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
