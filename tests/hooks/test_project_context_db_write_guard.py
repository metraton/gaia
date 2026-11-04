"""The gaia.db guard: the sqlite3 CLI never opens the store, and on any other database its reach is judged."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# hooks/modules/core/state.py imports `adapters` as a top-level name.
_HOOKS_DIR = str(Path(__file__).resolve().parents[2] / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from hooks.modules.security import gaia_db_write_guard as guard  # noqa: E402

check = guard.check
STORE = "~/.gaia/gaia.db"
OTHER = "/tmp/other.db"


def sqlite_unproven_effect(command, cwd):
    return guard.sqlite_unproven_effect(command, cwd)


def _refused_as_store(command, cwd=None):
    allowed, reason = check(command, cwd)
    assert allowed is False, command
    assert reason == guard.STORE_REJECTION_MESSAGE, (command, reason)
    assert "gaia context query" in reason


def _refused_unproven(command, cwd=None):
    allowed, reason = check(command, cwd)
    assert allowed is False, command
    assert "could not prove" in reason, (command, reason)
    assert "gaia context query" in reason


@pytest.mark.parametrize("sql", [
    "SELECT * FROM apps",
    "PRAGMA table_info(apps)",
    "PRAGMA journal_mode",
    ".schema plans",
    ".tables",
    "UPDATE apps SET status='x'",
    "PRAGMA journal_mode=DELETE",
    "VACUUM",
    "ATTACH DATABASE '/tmp/x.db' AS x",
    "REINDEX",
    'PRAGMA "user_version"=5',
    "PRAGMA [journal_mode]=delete",
    "PRAGMA/**/user_version=5",
    "PRAGMA main.\"user_version\"=5",
    "SELECT writefile('/home/jorge/.gaia/gaia.db', '')",
    "SELECT edit('x', 'touch /tmp/pwn')",
    "SELECT load_extension('/tmp/x.so')",
    ".load /tmp/x.so",
    ".sh kubectl apply -f x.yaml",
    ".system ls",
    "VACUUM INTO '/tmp/c.db'",
])
def test_sqlite3_on_the_store_is_refused_whatever_the_sql(sql):
    _refused_as_store(f'sqlite3 {STORE} "{sql}"')
    _refused_as_store(f"sqlite3 {STORE} <<EOF\n{sql}\nEOF")


@pytest.mark.parametrize("command", [
    f"sqlite3 /home/user/.gaia/gaia.db 'SELECT 1'",
    f"sqlite3 {STORE}",
    f"sqlite3 -readonly {STORE} 'SELECT 1'",
    f"sqlite3 '{STORE[2:]}'",
    "sqlite3 /srv/.gaia/GAIA.DB 'SELECT 1'",
    "sqlite3 /srv/.gaia/gaia.db-wal 'SELECT 1'",
    "sqlite3 file:/srv/.gaia/gaia.db?mode=ro 'SELECT 1'",
    "sqlite3 ~/.gaia/gaia.\"\"db 'DELETE FROM t'",
    "sqlite3 ~/.gaia/gai\\a.db 'DELETE FROM t'",
    f"sqlite3 -init /tmp/w.sql {STORE} 'SELECT 1'",
    f"HOME=/tmp/h sqlite3 {STORE} 'SELECT 1'",
    f"sqlite3 {STORE} '.once |sh' \"SELECT 'touch /tmp/pwn'\"",
    f"sqlite3 {STORE} \".output '|sh'\" \"SELECT 'touch /tmp/pwn'\"",
    f"sqlite3 -cmd '.restore /tmp/x.db' {STORE}",
    f"sqlite3 < /tmp/x.sql {STORE}",
    f"printf 'DELETE FROM t;' > /tmp/b.sql; sqlite3 {STORE} < /tmp/b.sql",
    f"printf 'DELETE FROM t;' > /tmp/b.sql; cat /tmp/b.sql | sqlite3 {STORE}",
    f"printf 'DELETE FROM t;' > /tmp/b.sql; sqlite3 {STORE} '.read /tmp/b.sql'",
    f"sqlite3 {STORE} '.cd /tmp/b' '.read w.sql'",
    f"echo hi | cat /tmp/b.sql | sqlite3 {STORE}",
    f"cat /tmp/w.sql | tee /dev/null | sqlite3 {STORE}",
    f"sudo sqlite3 {STORE} 'SELECT 1'",
    f"env FOO=1 timeout 5 sqlite3 {STORE} 'SELECT 1'",
    f"bash -c \"sqlite3 {STORE} 'SELECT 1'\"",
    f"echo $(sqlite3 {STORE} 'SELECT 1')",
    f"ls && /usr/bin/sqlite3 {STORE} .tables",
])
def test_every_spelling_that_opens_the_store_is_refused(command):
    _refused_as_store(command, cwd="/tmp")


def test_store_found_by_symlink_hardlink_and_relative_path(tmp_path, monkeypatch):
    store = tmp_path / "store.sqlite"
    store.write_bytes(b"")
    monkeypatch.setenv("GAIA_DB", str(store))
    os.symlink(store, tmp_path / "link.db")
    os.link(store, tmp_path / "hard.db")
    _refused_as_store(f"sqlite3 {tmp_path}/link.db 'SELECT 1'")
    _refused_as_store(f"sqlite3 {tmp_path}/hard.db 'SELECT 1'")
    _refused_as_store("sqlite3 hard.db 'SELECT 1'", cwd=str(tmp_path))
    assert check(f"sqlite3 {tmp_path}/fresh.db 'SELECT 1'") == (True, None)


@pytest.mark.parametrize("command,cwd", [
    ("sqlite3 ~/.gaia/gaia.d? 'DELETE FROM t'", "/tmp"),
    ("D=~/.gaia/gaia.db; sqlite3 $D 'DELETE FROM t'", "/tmp"),
    ("sqlite3 \"$GAIA_DB\" 'SELECT 1'", "/tmp"),
    ("sqlite3 $(ls ~/.gaia/*.db) 'SELECT 1'", "/tmp"),
    ("sqlite3 `cat /tmp/p` 'SELECT 1'", "/tmp"),
    ("sqlite3 ~/.gaia/{gaia,x}.db 'SELECT 1'", "/tmp"),
    ("sqlite3 ~jorge/notes.db 'SELECT 1'", "/tmp"),
    (f"echo {STORE} | xargs -I{{}} sqlite3 {{}} 'SELECT 1'", "/tmp"),
    ("sqlite3 local.db 'SELECT 1'", None),
    ("cd /x && sqlite3 local.db 'SELECT 1'", "/tmp"),
    (f"sqlite3 {OTHER} \".open $DB\"", "/tmp"),
    (f"sqlite3 {OTHER} \"ATTACH 'gaia.' || 'db' AS g\"", "/tmp"),
])
def test_a_database_the_guard_cannot_resolve_is_refused_as_unproven(command, cwd):
    _refused_unproven(command, cwd)


@pytest.mark.parametrize("command", [
    f"sqlite3 {OTHER} '.open {STORE}'",
    f"sqlite3 {OTHER} \"ATTACH DATABASE '{STORE}' AS g\"",
    f"sqlite3 {OTHER} \".backup main /home/user/.gaia/gaia.db\"",
    f"sqlite3 {OTHER} \"VACUUM INTO '/srv/.gaia/gaia.db'\"",
    f"sqlite3 {OTHER} \"SELECT writefile('/srv/.gaia/gaia.db-wal', x'00')\"",
    f"sqlite3 {OTHER} <<EOF\n.output /home/user/.gaia/gaia.db\nEOF",
    f"sqlite3 {OTHER} '.system cp /tmp/x {STORE}'",
])
def test_another_database_reaching_the_store_is_refused(command):
    _refused_as_store(command, cwd="/tmp")


README = Path(__file__).resolve().parents[2] / "README.md"


@pytest.mark.parametrize("command", [
    f"sqlite3 {OTHER} '.shell rm -rf /'",
    f"echo .shell rm -rf / | sqlite3 {OTHER}",
    f"sqlite3 -readonly -safe {OTHER} '.system rm -rf /'",
    f"sqlite3 {OTHER} '.once |cat ~/.ssh/id_rsa'",
    f"sqlite3 {OTHER} \".once --bom '|cat ~/.ssh/id_rsa'\" 'SELECT 1'",
    f"sqlite3 {OTHER} \".import '|cat ~/.ssh/id_rsa' t\"",
    f"sqlite3 {OTHER} \"SELECT \\\"edit\\\"('x', 'rm -rf /')\"",
    f"sqlite3 {OTHER} '.output {README}' 'SELECT 1'",
    f"sqlite3 {OTHER} \"SELECT [writefile]('{README}', 'x')\"",
    f"sqlite3 {OTHER} \".read '|rm -rf /'\"",
    f"sqlite3 {OTHER} \".READ '|rm -rf /'\"",
    f"sqlite3 {OTHER} \".re '|cat ~/.ssh/id_rsa'\"",
    f"sqlite3 -cmd \".log '|rm -rf /'\" {OTHER}",
    f"sqlite3 {OTHER} <<'EOF'\n.read '|rm -rf /'\nEOF",
    f"sqlite3 {OTHER} \".read '|sed -i s/a/b/ {README}'\"",
    f"VISUAL='rm -rf /' sqlite3 {OTHER} \"SELECT edit('x')\"",
    f"env EDITOR='cat ~/.ssh/id_rsa' sqlite3 {OTHER} \"SELECT edit('x')\"",
    f"VISUAL='rm -rf /' sqlite3 {OTHER} '.once -e' 'SELECT 1'",
])
def test_a_readable_payload_meets_the_floors_a_bash_line_meets(command):
    allowed, reason = check(command, "/tmp")
    assert allowed is False, command
    assert reason.startswith(guard.REACH_REJECTION_PREFIX), (command, reason)
    assert _validate(command).approval_id is None


@pytest.mark.parametrize("command", [
    f"sqlite3 {OTHER} <<EOF\nSELECT 1;\nEOF",
    "sqlite3 -version",
    f"env -u HOME sqlite3 {OTHER} 'SELECT 1'",
])
def test_another_database_in_the_read_subset_signs(command):
    assert check(command, "/tmp") == (True, None)
    assert sqlite_unproven_effect(command, "/tmp") == guard.SIGN_GUIDANCE


@pytest.mark.parametrize("command", [
    f"sqlite3 {OTHER} '.shell ls /tmp'",
    f"sqlite3 {OTHER} '.output /tmp/out.txt' 'SELECT 1'",
    f"sqlite3 {OTHER} '.shell kubectl apply -f x.yaml'",
    f"sqlite3 {OTHER} '.sys git push --force origin main'",
    f"sqlite3 {OTHER} '.shell echo $(id)'",
    f"sqlite3 {OTHER} '.once |sh' \"SELECT 'touch /tmp/pwn'\"",
    f"sqlite3 {OTHER} \".output '|sh'\" \"SELECT 'touch /tmp/pwn'\"",
    f"sqlite3 {OTHER} '.once -e' 'SELECT 1'",
    f"sqlite3 {OTHER} '.excel' 'SELECT 1'",
    f"HOME=/tmp/h sqlite3 {OTHER} 'SELECT 1'",
    f"XDG_CONFIG_HOME=/tmp/h sqlite3 {OTHER} 'SELECT 1'",
    f"sqlite3 {OTHER} '.load /tmp/x.so'",
    f"sqlite3 {OTHER} \"SELECT load_extension('/tmp/x.so')\"",
    f"sqlite3 {OTHER} \"SELECT edit('x', 'touch /tmp/pwn')\"",
    f"sqlite3 {OTHER} \"SELECT writefile(name, data) FROM t\"",
    f"sqlite3 {OTHER} \"SELECT writefile/**/('/tmp/' || name, data) FROM t\"",
    f"sqlite3 {OTHER} '.output $OUT' 'SELECT 1'",
    f"sqlite3 {OTHER} '.cd /tmp' 'SELECT 1'",
    f"sqlite3 -A -x -f /tmp/a.sqlar",
    f"sqlite3 {OTHER} '.ar -x'",
    f"sqlite3 {OTHER} <<EOF\n.shell kubectl apply -f x.yaml\nEOF",
    f"VISUAL=vim sqlite3 {OTHER} \"SELECT edit('x')\"",
])
def test_another_database_outside_the_read_subset_is_refused(command):
    allowed, reason = check(command, "/tmp")
    assert allowed is False, command
    assert "gaia context query" in reason or reason.startswith(guard.REACH_REJECTION_PREFIX), reason


@pytest.mark.parametrize("command", [
    f"gaia memory add --body \"never run sqlite3 {STORE} 'SELECT 1'\"",
    f"echo sqlite3 {STORE}",
    "grep -rn sqlite3 /tmp/*.py",
    "ls -la /home/user",
])
def test_a_mention_of_sqlite3_is_not_an_invocation(command):
    assert check(command, "/tmp") == (True, None)
    assert sqlite_unproven_effect(command, "/tmp") is None


def _validate(command):
    from hooks.modules.tools.bash_validator import BashValidator

    return BashValidator().validate(
        command, is_subagent=True, session_id="ses-db-guard", agent_type="gaia-system", cwd="/tmp",
    )


def test_validator_refuses_the_store_and_the_outside_subset_and_signs_the_subset():
    refused = _validate(f"sqlite3 {STORE} 'SELECT 1'")
    assert refused.rule == "floor.gaia-db-write"
    assert refused.reason.startswith(guard.STORE_REJECTION_MESSAGE)

    outside = _validate(f"sqlite3 {OTHER} '.once |sh' \"SELECT 'touch /tmp/pwn'\"")
    assert outside.rule == "floor.gaia-db-write"
    assert outside.approval_id is None

    signed = _validate(f"sqlite3 {OTHER} '.tables'")
    assert signed.allowed is False
    assert signed.approval_id

    deleted = _validate(f'sqlite3 {OTHER} "DELETE FROM t"')
    assert deleted.allowed is False
    assert deleted.rule == "sign.destroys"


@pytest.mark.parametrize("command", [
    "cp /tmp/x /srv/.gaia/gaia.db",
    "echo x > ~/.gaia/gaia.db-wal",
    "dd if=/dev/zero of=/srv/.gaia/gaia.db",
])
def test_shell_writer_over_the_store_file_refused(command):
    assert check(command) == (False, guard.FILE_WRITE_REJECTION_MESSAGE)


FREE = f"sqlite3 -readonly -safe {OTHER}"


@pytest.mark.parametrize("command", [
    f"echo .open {STORE} | sqlite3 {OTHER}",
    f"printf '.open %s\\nPRAGMA journal_mode=DELETE;\\n' {STORE} | sqlite3 {OTHER}",
    f"echo {STORE} VACUUM | xargs sqlite3",
    f"echo '.open /home/jorge/.gaia/gaia.db' | sqlite3",
    f"sqlite3 {OTHER} <<EOF\n  .open {STORE}\nEOF",
    f"sqlite3 {OTHER} <<< '  .open {STORE}'",
    f"{FREE} 'SELECT 1' '.open {STORE}'",
])
def test_the_store_in_content_fed_to_sqlite3_is_refused(command):
    _refused_as_store(command, cwd="/tmp")


def test_the_store_in_fed_content_is_found_by_inode_and_in_a_redirected_file(tmp_path, monkeypatch):
    store = tmp_path / "store.sqlite"
    store.write_bytes(b"")
    monkeypatch.setenv("GAIA_DB", str(store))
    os.link(store, tmp_path / "hard.db")
    script = tmp_path / "s.sql"
    script.write_text(f".open {tmp_path}/hard.db\n")
    _refused_as_store(f"printf '.open %s\\n' {tmp_path}/hard.db | sqlite3 {OTHER}", cwd="/tmp")
    _refused_as_store(f"sqlite3 {OTHER} < {script}", cwd="/tmp")


def test_every_file_sqlite3_is_made_to_read_meets_the_checks_of_a_redirected_one(tmp_path):
    opener = tmp_path / "open.sql"
    opener.write_text(f".open {STORE}\nPRAGMA journal_mode=DELETE;\n")
    (tmp_path / "nested.sql").write_text(f"SELECT 1;\n.read '|cat {opener}'\n")
    (tmp_path / "h").mkdir()
    (tmp_path / "h" / ".sqliterc").write_text(".read nested.sql\n")
    (tmp_path / "x" / "sqlite3").mkdir(parents=True)
    (tmp_path / "x" / "sqlite3" / "sqliterc").write_text(opener.read_text())
    (tmp_path / "shell.sql").write_text(".shell rm -rf /\n")
    for command in (
        f"sqlite3 {OTHER} < {opener}",
        f"sqlite3 -init {opener} {OTHER} 'SELECT 1'",
        f"sqlite3 {OTHER} '.read {opener}'",
        f"sqlite3 -cmd '.read {opener}' {OTHER}",
        f"sqlite3 {OTHER} '.read nested.sql'",
        f"sqlite3 {OTHER} \".read '|cat {opener}'\"",
        f"sqlite3 {OTHER} < <(cat {opener})",
        f"cat {opener} | sqlite3 {OTHER}",
        f"HOME={tmp_path}/h sqlite3 {OTHER} 'SELECT 1'",
        f"env XDG_CONFIG_HOME={tmp_path}/x sqlite3 {OTHER} 'SELECT 1'",
    ):
        _refused_as_store(command, cwd=str(tmp_path))
    allowed, reason = check(f"sqlite3 -init {tmp_path}/shell.sql {OTHER}", "/tmp")
    assert allowed is False and reason.startswith(guard.REACH_REJECTION_PREFIX), reason
    assert _validate(f"sqlite3 -init {opener} {OTHER} 'SELECT 1'").approval_id is None
    for unproven in (
        f"sqlite3 -init {tmp_path}/absent.sql {OTHER}",
        f"sqlite3 {OTHER} '.read {tmp_path}'",
        f"sqlite3 {OTHER} < <(cat {tmp_path}/*.sql)",
        f"sqlite3 {OTHER} '.read nested.sql'",
        f"sqlite3 -init /tmp/absent-1097.sql {OTHER} 'SELECT 1'",
        f"sqlite3 {OTHER} '.read /tmp/absent-1097.sql'",
        f"sqlite3 {OTHER} < /tmp/absent-1097.sql",
        f"cat /tmp/absent-1097.sql | sqlite3 {OTHER}",
        f"sqlite3 {OTHER} \".read '|cat /tmp/absent-1097.sql'\"",
    ):
        _refused_unproven(unproven, None)


@pytest.fixture()
def cd_folder(tmp_path, monkeypatch):
    store = tmp_path / "store.sqlite"
    store.write_bytes(b"")
    monkeypatch.setenv("GAIA_DB", str(store))
    folder = tmp_path / "d"
    folder.mkdir()
    os.link(store, folder / "hl.db")
    (folder / "evil.sql").write_text(".shell rm -rf /\n")
    (folder / "cd.sql").write_text(f".cd {README.parent}\n.once README.md\nSELECT 1;\n")
    return folder


@pytest.mark.parametrize("command", [
    "sqlite3 {o} '.cd {d}' '.open hl.db' 'PRAGMA journal_mode=DELETE'",
    "sqlite3 -cmd '.cd {d}' -cmd '.open hl.db' {o}",
    "sqlite3 {o} '.cd {d}/..' '.cd d' '.open hl.db'",
    "sqlite3 {o} <<'EOF'\n.cd {d}\n.open hl.db\nEOF",
    "printf '.cd {d}\\n.open hl.db\\n' | sqlite3 {o}",
    "sqlite3 {o} '.cd {d}' \"ATTACH 'hl.db' AS g\"",
    "sqlite3 {o} '.cd {d}' '.save hl.db'",
    "sqlite3 {o} '.cd {d}' '.import hl.db t'",
])
def test_a_relative_operand_after_a_literal_sqlite3_cd_reaching_the_store_is_refused(cd_folder, command):
    _refused_as_store(command.format(o=OTHER, d=cd_folder), cwd="/tmp")


@pytest.mark.parametrize("command", [
    "sqlite3 -cmd '.cd {d}' -cmd '.read evil.sql' {o}",
    "sqlite3 {o} '.cd {d}' '.read evil.sql'",
    "sqlite3 {o} '.cd {repo}' '.once README.md' \"SELECT 'x'\"",
    "sqlite3 -cmd '.cd {repo}' -cmd '.output README.md' {o} 'SELECT 1'",
    "sqlite3 {o} '.cd {repo}' '.log README.md' 'SELECT 1'",
    "sqlite3 {o} '.cd {repo}' '.shell echo x > README.md'",
    "sqlite3 {o} '.read {d}/cd.sql'",
    "sqlite3 {o} '.cd {d}' '.read cd.sql'",
])
def test_a_relative_operand_after_a_literal_sqlite3_cd_meets_the_shell_floors_there(cd_folder, command):
    command = command.format(o=OTHER, d=cd_folder, repo=README.parent)
    allowed, reason = check(command, "/tmp")
    assert allowed is False and reason.startswith(guard.REACH_REJECTION_PREFIX), (command, reason)
    assert check(f"sqlite3 {OTHER} '.tables'", "/tmp") == (True, None)


@pytest.mark.parametrize("command,cwd", [
    (f"sqlite3 {OTHER} '.cd $D' '.open hl.db'", "/tmp"),
    (f"sqlite3 {OTHER} '.cd $D' '.once README.md' 'SELECT 1'", "/tmp"),
    (f"sqlite3 -cmd '.cd /tmp/*' -cmd '.read evil.sql' {OTHER}", "/tmp"),
    (f"sqlite3 {OTHER} '.cd sub' '.output out.txt'", None),
    (f"echo '.cd `pwd`' | sqlite3 {OTHER} '.import data.csv t'", "/tmp"),
])
def test_a_sqlite3_cd_the_guard_cannot_resolve_leaves_later_relative_operands_unproven(
    command, cwd, tmp_path, monkeypatch,
):
    for process_folder in (README.parent, tmp_path):
        monkeypatch.chdir(process_folder)
        _refused_unproven(command, cwd)
        assert check(f"sqlite3 {OTHER} '.tables'", "/tmp") == (True, None)


@pytest.mark.parametrize("command", [
    "env -C {d} sqlite3 s.db 'PRAGMA journal_mode=DELETE'",
    "env --chdir={d} sqlite3 hl.db 'VACUUM'",
    "sudo -D {d} sqlite3 s.db 'REINDEX'",
    "find {d}/sub -maxdepth 0 -execdir sqlite3 s.db 'PRAGMA journal_mode=DELETE' ;",
    "echo x | xargs -I{{}} env -C {d} sqlite3 hl.db 'VACUUM'",
    "nice sqlite3 s.db 'VACUUM'",
    "'cd' {d}; sqlite3 s.db 'VACUUM'",
    "\"cd\" {d} && sqlite3 s.db 'VACUUM'",
    "\\cd {d}; sqlite3 s.db 'VACUUM'",
    "c''d {d}; sqlite3 s.db 'VACUUM'",
    "source {d}/cd.sh; sqlite3 s.db 'VACUUM'",
    ". {d}/cd.sh; sqlite3 s.db 'VACUUM'",
    "source {d}/cd.sh && echo VACUUM | xargs sqlite3 s.db",
    "timeout 5 env -C {d} sqlite3 s.db 'VACUUM'",
    "env --chdir {d} sqlite3 s.db 'VACUUM'",
    "env -C{d} sqlite3 s.db 'VACUUM'",
    "sudo --chdir={d} sqlite3 s.db 'VACUUM'",
    "flock /tmp/l env -C {d} sqlite3 s.db 'VACUUM'",
    "nohup setsid env -C {d} sqlite3 s.db 'VACUUM'",
    "stdbuf -o0 nice -n 5 env -C {d} sqlite3 s.db 'VACUUM'",
    "script -q -c \"env -C {d} sqlite3 s.db VACUUM\" /dev/null",
    "busybox env -C {d} sqlite3 s.db 'VACUUM'",
    "ls; sqlite3 s.db 'VACUUM'",
    "sqlite3 /tmp/o.db 'SELECT 1'; sqlite3 s.db 'VACUUM'",
    "sqlite3 s.db 'VACUUM'; cd {d}; sqlite3 s.db 'VACUUM'",
    "sqlite3 s.db VACUUM && cd {d} && sqlite3 s.db VACUUM",
    "sqlite3 s.db VACUUM\ncd {d}\nsqlite3 s.db VACUUM",
    "sqlite3 s.db VACUUM; pushd {d}; sqlite3 s.db VACUUM",
    "sqlite3 s.db VACUUM & cd {d}; sqlite3 s.db VACUUM",
    "sqlite3 s.db VACUUM || (cd {d} && sqlite3 s.db VACUUM)",
    "sqlite3 hl.db 'PRAGMA journal_mode=DELETE'; cd {d}; sqlite3 hl.db 'PRAGMA journal_mode=DELETE'",
    "sqlite3 s.db VACUUM; 'cd' {d}; sqlite3 s.db VACUUM",
    "cd {d}\nsqlite3 s.db VACUUM",
    "(cd {d}; sqlite3 s.db VACUUM)",
    "{{ cd {d}; sqlite3 s.db VACUUM; }}",
])
def test_a_relative_database_with_anything_before_sqlite3_on_the_line_is_unproven(cd_folder, command):
    os.symlink(cd_folder / "hl.db", cd_folder / "s.db")
    (cd_folder / "sub").mkdir()
    (cd_folder / "cd.sh").write_text(f"cd {cd_folder}\n")
    _refused_unproven(command.format(d=cd_folder), cwd="/tmp")
    assert check(f"env -C {cd_folder} sqlite3 {OTHER} 'SELECT 1'", "/tmp") == (True, None)


@pytest.mark.parametrize("command,cwd", [
    ("cd {d} && sqlite3 /tmp/o.db < evil.sql", "/tmp"),
    ("cd {d} && sqlite3 -init evil.sql /tmp/o.db", "/tmp"),
    ("cd {d} && HOME=. sqlite3 /tmp/o.db 'SELECT 1'", "/tmp"),
    ("cd {d} && XDG_CONFIG_HOME=cfg sqlite3 /tmp/o.db 'SELECT 1'", "/tmp"),
    ("sqlite3 /tmp/o.db 'SELECT 1'; cd {d}; sqlite3 /tmp/o.db < evil.sql", "/tmp"),
    ("cd {d} && cat evil.sql | sqlite3 /tmp/o.db", "/tmp"),
    ("cd {d} && sqlite3 /tmp/o.db '.read evil.sql'", "/tmp"),
    ("sqlite3 -init evil.sql /tmp/o.db", None),
])
def test_a_relative_file_sqlite3_reads_from_an_unproven_folder_is_unproven(cd_folder, command, cwd):
    _refused_unproven(command.format(d=cd_folder), cwd)
    (cd_folder / "ok.sql").write_text("SELECT 1;\n")
    assert check(f"cd {cd_folder} && sqlite3 {OTHER} < {cd_folder}/ok.sql", "/tmp") == (True, None)


@pytest.fixture()
def opener(cd_folder):
    (cd_folder / "open.sql").write_text(f".open {os.environ['GAIA_DB']}\nPRAGMA user_version=9;\n")
    (cd_folder / "ok.sql").write_text("SELECT 1;\n")
    return cd_folder


@pytest.mark.parametrize("command", [
    "< {d}/open.sql sqlite3 {o}",
    "0< {d}/open.sql sqlite3 {o}",
    "0<{d}/open.sql sqlite3 {o}",
    "X=1 < {d}/open.sql sqlite3 {o}",
    "2>/dev/null < {d}/open.sql sqlite3 {o}",
    "{{ sqlite3 {o}; }} < {d}/open.sql",
    "exec < {d}/open.sql; sqlite3 {o}",
    "cat -- {d}/open.sql | sqlite3 {o}",
])
def test_sqlite3_stdin_from_any_redirect_or_proven_feeder_is_judged_by_its_content(opener, command):
    _refused_as_store(command.format(d=opener, o=OTHER), cwd="/tmp")


@pytest.mark.parametrize("command", [
    "cd {d} && < open.sql sqlite3 {o}",
    "head -n 99 {d}/open.sql | sqlite3 {o}",
    "python3 /tmp/emit.py | sqlite3 {o}",
    "sqlite3 {o} <&3",
    "sqlite3 {o} < {d}/absent.sql",
    "sqlite3 {o} < <(head {d}/open.sql)",
    "sqlite3 {o} \".read '|head {d}/open.sql'\"",
    "cd {d} && sqlite3 {o} '.load ./ext'",
    "cd {d} && sqlite3 {o} \"SELECT load_extension('./ext')\"",
])
def test_sqlite3_input_the_guard_cannot_read_in_full_is_unproven(opener, command):
    _refused_unproven(command.format(d=opener, o=OTHER), cwd="/tmp")
    for free in (f"< {opener}/ok.sql sqlite3 {OTHER}", f"echo 'SELECT 1;' | sqlite3 {OTHER}"):
        assert check(free, "/tmp") == (True, None), free
        assert sqlite_unproven_effect(free, "/tmp") == guard.SIGN_GUIDANCE


@pytest.mark.parametrize("command", [
    "sqlite3 /tmp/a.sqlar -Ax -C {d}",
    "sqlite3 /tmp/a.sqlar '.ar -x -C {d}'",
    "sqlite3 /tmp/a.sqlar '.ar --extract --directory {d}'",
    "cd {d} && sqlite3 /tmp/a.sqlar '.arc -x'",
    "cd {d} && sqlite3 /tmp/a.sqlar -archive -x",
    "cd {d} && sqlite3 /tmp/a.sqlar --Ax",
    "cd {d} && sqlite3 /tmp/a.sqlar '.a -x'",
    "sqlite3 {d}/o.db '.trace hl.db' 'SELECT 1'",
    "sqlite3 {d}/o.db \"SELECT writefile('hl.db', 'x')\"",
    "sqlite3 {o} $'.op\\x65n {d}/hl.db'",
    "sqlite3 {o} $'ATT\\x41CH \\'/tmp/x.db\\' AS y'",
    "time -p sqlite3 {o} < {d}/open.sql",
    "HOME=/tmp sqlite3 {o} 'SELECT 1'",
    "sqlite3 -cmd '.tables' {o}",
    "sqlite3 -init /dev/null {o}",
    "sqlite3 {o} \"SELECT name FROM fsdir('/tmp')\"",
    "sqlite3 {o} '.header on' 'SELECT 1'",
    "sqlite3 {o} '.output /tmp/out.txt' 'SELECT 1'",
    "sqlite3 {o} '.shell ls /tmp'",
    "sqlite3 ./rel.db 'SELECT 1'",
    "sqlite3 'file:/tmp/o.db?vfs=unix' 'SELECT 1'",
    "sqlite3 {o} \"VACUUM INTO '/tmp/c.db'\"",
])
def test_sqlite3_outside_the_read_subset_is_refused(opener, command):
    allowed, reason = check(command.format(d=opener, o=OTHER), str(opener))
    assert allowed is False, command
    assert "gaia context query" in reason


@pytest.mark.parametrize("command", [
    "sqlite3 -readonly -json {o} '.tables' '.schema t' 'SELECT 1'",
    "sqlite3 --safe {o} '.headers on' '.mode csv' 'SELECT * FROM t'",
    "sqlite3 {o} '.dump'",
    "sqlite3 {o} <<'EOF'\n.tables\nSELECT 1;\nEOF",
    "sqlite3 {o} < {d}/ok.sql",
    "sqlite3 -separator , {o} 'SELECT 1'",
    "sqlite3 :memory: 'SELECT 1'",
])
def test_sqlite3_inside_the_read_subset_signs(opener, command):
    command = command.format(d=opener, o=OTHER)
    assert check(command, "/tmp") == (True, None), command
    assert sqlite_unproven_effect(command, "/tmp") == guard.SIGN_GUIDANCE


def test_an_unproven_folder_with_no_sqlite3_cd_does_not_blame_cd(opener):
    allowed, reason = check(f"ls; sqlite3 {OTHER} < ok.sql", str(opener))
    assert allowed is False and "could not prove" in reason
    assert "`.cd`" not in reason


@pytest.mark.parametrize("command", [
    "sqlite3 {o} \"$(head {d}/open.sql)\"",
    "sqlite3 {o} \"$SQL\"",
    "sqlite3 {o} \"SELECT $X\"",
    "sqlite3 {o} `head {d}/open.sql`",
    "env -C /tmp sqlite3 {o} \"$SQL\"",
    "sqlite3 {o} <<EOF\n$SQL\nEOF",
    "HOME=$PWD sqlite3 {o} 'SELECT 1'",
    "cd {d} && sqlite3 {o} \"SELECT readfile('hl.db')\"",
    "cd {d} && sqlite3 {o} \"SELECT \\\"writefile\\\"('x.db', 1)\"",
    "cd {d} && sqlite3 -A -x -f /tmp/a.sqlar",
    "cd {d} && sqlite3 -Axf /tmp/a.sqlar",
    "cd {d} && sqlite3 {o} '.ar -x'",
])
def test_sqlite3_text_the_shell_computes_or_a_relative_file_function_is_unproven(opener, command):
    _refused_unproven(command.format(d=opener, o=OTHER), cwd="/tmp")
    for free in (f"sqlite3 {OTHER} '.schema' 'SELECT 1'", f"sqlite3 {OTHER} <<'EOF'\nSELECT '$SQL';\nEOF"):
        assert check(free, "/tmp") == (True, None), free


@pytest.mark.parametrize("command", [
    f"echo .shell touch /tmp/pwn | sqlite3 {OTHER}",
    f"printf '.%s kubectl delete ns prod\\n' shell | sqlite3 {OTHER}",
    f"sqlite3 {OTHER} \"SELECT \\\"edit\\\"('x', 'touch /tmp/pwn')\"",
    f"sqlite3 {OTHER} \"SELECT [writefile]('/tmp/w', 'x')\"",
    f"sqlite3 {OTHER} \"SELECT `load_extension`('/tmp/x.so')\"",
    f"sqlite3 {OTHER} \".import '|touch /tmp/pwn' t\"",
    f"sqlite3 {OTHER} \".once --bom '|sh'\" \"SELECT 'touch /tmp/pwn'\"",
    f"sqlite3 {OTHER} '.output --bom |kubectl apply -f -' 'SELECT 1'",
    f"sqlite3 {OTHER} \".once --bom '|git push --force origin main'\" 'SELECT 1'",
    f"sqlite3 {OTHER} '.testcase x' 'SELECT 1'",
    f"sqlite3 {OTHER} 'SELECT 1'",
    f"sudo sqlite3 {OTHER} 'SELECT 1'",
    f"sudo -H {FREE} 'SELECT 1'",
    f"sqlite3 -readonly {OTHER} 'SELECT 1'",
    f"sqlite3 -safe {OTHER} 'SELECT 1'",
    f"{FREE} -nonce n '.nonce n' '.shell touch /tmp/pwn'",
    f"{FREE} -unsafe-testing 'SELECT 1'",
    f"sqlite3 -readonly -safe -init /tmp/w.sql {OTHER} 'SELECT 1'",
    f"{FREE} -cmd '.tables' 'SELECT 1'",
    f"HOME=/tmp/h {FREE} 'SELECT 1'",
    f"{FREE} '.testcase x'",
    f"{FREE} '.import /tmp/x.csv t'",
    f"{FREE} \"SELECT '$(id)'\"",
    f"{FREE} 'SELECT 1' && ls",
    f"echo 'SELECT 1;' | {FREE}",
    f"{FREE} <<< 'SELECT 1'",
    f"{FREE} < /tmp/x.sql",
    f"{FREE} 'SELECT 1' > /tmp/out",
    f"{FREE}",
    "sqlite3 -readonly -safe file:/tmp/other.db 'SELECT 1'",
    f"/opt/x/sqlite3 -readonly -safe {OTHER} 'SELECT 1'",
    f"{FREE} \"VACUUM INTO '/tmp/c.db'\"",
])
def test_every_sqlite3_call_on_another_database_is_at_least_signed(command):
    assert check(command, "/tmp")[1] != guard.STORE_REJECTION_MESSAGE
    assert sqlite_unproven_effect(command, "/tmp"), command
    verdict = _validate(command)
    assert verdict.allowed is False, command


@pytest.mark.parametrize("command", [
    f"{FREE} 'SELECT 1'",
    f"sqlite3 --safe --readonly -json -header {OTHER} 'SELECT * FROM t' '.tables'",
])
def test_readonly_safe_signs_since_sqlite3_3_45_1_safe_mode_still_reads_files_through_fsdir(command):
    assert check(command, "/tmp") == (True, None)
    assert sqlite_unproven_effect(command, "/tmp") == guard.SIGN_GUIDANCE
    verdict = _validate(command)
    assert verdict.approval_id, command
    assert "fsdir()" in str(verdict.block_response)
    for reader in (f"{FREE} \"SELECT name, data FROM fsdir('/tmp')\"", f"{FREE} \"SELECT name FROM \\\"fsdir\\\"('/tmp')\""):
        assert check(reader, "/tmp")[0] is False, reader


@pytest.mark.parametrize("init_file", ["~/.sqliterc", "~/.config/sqlite3/sqliterc"])
def test_writing_a_sqlite3_init_file_signs(init_file):
    from hooks.modules.security.sensitive_paths import is_account_path

    assert is_account_path(os.path.expanduser(init_file))
    verdict = _validate(f"echo .shell id > {init_file}")
    assert verdict.allowed is False and verdict.approval_id
