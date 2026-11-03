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
])
def test_a_readable_payload_meets_the_floors_a_bash_line_meets(command):
    allowed, reason = check(command, "/tmp")
    assert allowed is False, command
    assert reason.startswith(guard.REACH_REJECTION_PREFIX), (command, reason)
    assert _validate(command).approval_id is None


@pytest.mark.parametrize("command", [
    f"sqlite3 {OTHER} '.shell ls /tmp'",
    f"sqlite3 {OTHER} '.output /tmp/out.txt' 'SELECT 1'",
    f"sqlite3 {OTHER} <<EOF\nSELECT 1;\nEOF",
    "sqlite3 -version",
    f"sqlite3 {OTHER} '.shell kubectl apply -f x.yaml'",
    f"sqlite3 {OTHER} '.sys git push --force origin main'",
    f"sqlite3 {OTHER} '.shell echo $(id)'",
    f"sqlite3 {OTHER} '.once |sh' \"SELECT 'touch /tmp/pwn'\"",
    f"sqlite3 {OTHER} \".output '|sh'\" \"SELECT 'touch /tmp/pwn'\"",
    f"sqlite3 {OTHER} '.once -e' 'SELECT 1'",
    f"sqlite3 {OTHER} '.excel' 'SELECT 1'",
    f"sqlite3 -init /tmp/w.sql {OTHER} 'SELECT 1'",
    f"HOME=/tmp/h sqlite3 {OTHER} 'SELECT 1'",
    f"XDG_CONFIG_HOME=/tmp/h sqlite3 {OTHER} 'SELECT 1'",
    f"env -u HOME sqlite3 {OTHER} 'SELECT 1'",
    f"sqlite3 {OTHER} '.load /tmp/x.so'",
    f"sqlite3 {OTHER} \"SELECT load_extension('/tmp/x.so')\"",
    f"sqlite3 {OTHER} \"SELECT edit('x', 'touch /tmp/pwn')\"",
    f"sqlite3 {OTHER} \"SELECT writefile(name, data) FROM t\"",
    f"sqlite3 {OTHER} \"SELECT writefile/**/('/tmp/' || name, data) FROM t\"",
    f"sqlite3 {OTHER} '.output $OUT' 'SELECT 1'",
    f"sqlite3 {OTHER} '.read /tmp/w.sql'",
    f"sqlite3 {OTHER} < /tmp/w.sql",
    f"cat /tmp/w.sql | sqlite3 {OTHER}",
    f"python3 /tmp/emit.py | sqlite3 {OTHER}",
    f"sqlite3 {OTHER} '.cd /tmp' 'SELECT 1'",
    f"sqlite3 -A -x -f /tmp/a.sqlar",
    f"sqlite3 {OTHER} '.ar -x'",
    f"sqlite3 {OTHER} <<EOF\n.shell kubectl apply -f x.yaml\nEOF",
])
def test_another_database_signs_when_nothing_it_reaches_is_refused(command):
    assert check(command, "/tmp") == (True, None)
    assert sqlite_unproven_effect(command, "/tmp") == guard.SIGN_GUIDANCE


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


def test_validator_refuses_the_store_categorically_and_signs_an_unproven_reach():
    refused = _validate(f"sqlite3 {STORE} 'SELECT 1'")
    assert refused.rule == "floor.gaia-db-write"
    assert refused.reason.startswith(guard.STORE_REJECTION_MESSAGE)

    signed = _validate(f"sqlite3 {OTHER} '.once |sh' \"SELECT 'touch /tmp/pwn'\"")
    assert signed.allowed is False
    assert signed.rule != "floor.gaia-db-write"
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
    f"{FREE} \"SELECT name, data FROM fsdir('/tmp')\"",
    f"{FREE} \"SELECT name FROM \\\"fsdir\\\"('/tmp')\"",
])
def test_readonly_safe_signs_since_sqlite3_3_45_1_safe_mode_still_reads_files_through_fsdir(command):
    assert check(command, "/tmp") == (True, None)
    assert sqlite_unproven_effect(command, "/tmp") == guard.SIGN_GUIDANCE
    verdict = _validate(command)
    assert verdict.approval_id, command
    assert "fsdir()" in str(verdict.block_response)


@pytest.mark.parametrize("init_file", ["~/.sqliterc", "~/.config/sqlite3/sqliterc"])
def test_writing_a_sqlite3_init_file_signs(init_file):
    from hooks.modules.security.sensitive_paths import is_account_path

    assert is_account_path(os.path.expanduser(init_file))
    verdict = _validate(f"echo .shell id > {init_file}")
    assert verdict.allowed is False and verdict.approval_id
