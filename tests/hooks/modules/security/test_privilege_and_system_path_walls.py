"""sudo/su/doas and shell writes under /etc, /usr, /boot sign under their own rules, and a granted approval lets the exact command run."""

import re
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "hooks"))

from modules.security.approval_grants import activate_db_pending_by_id
from modules.tools.bash_validator import validate_bash_command
from tests.hooks.modules.security.test_approval_cycle import (  # noqa: F401
    _isolate_writer_db,
    approvals_test_db,
    clean_grants_dir,
)

SESSION = "test-cycle-session"


def _validate(command):
    return validate_bash_command(command, is_subagent=True, agent_type="gaia-system", session_id=SESSION)


@pytest.mark.parametrize("command", ["sudo ls", "doas apt update", "su - root", "env FOO=1 sudo -u bob cat /tmp/x"])
def test_privilege_programs_sign_under_their_own_rule(command):
    result = _validate(command)
    assert not result.allowed
    assert result.approval_id
    assert result.rule == "sign.privilege"


@pytest.mark.parametrize("command", ["sudo rm -rf /", "doas mkfs.ext4 /dev/sda1", "dd if=/tmp/a of=/boot/gaia-probe"])
def test_privilege_payload_still_meets_the_floors(command):
    result = _validate(command)
    assert not result.allowed
    assert not result.approval_id
    assert result.rule.startswith("floor.")


@pytest.mark.parametrize("command", ["echo x > /etc/gaia-probe", "tee /usr/local/gaia-probe", "sed -i s/a/b/ /boot/gaia-probe"])
def test_shell_write_under_system_dirs_signs(command):
    result = _validate(command)
    assert not result.allowed
    assert result.approval_id
    assert result.rule == "sign.system-path"


def test_shell_write_elsewhere_keeps_its_verdict(tmp_path):
    assert _validate(f"echo x > {tmp_path}/probe").allowed
    assert not _validate("echo sudo").approval_id


@pytest.mark.parametrize("command", ["sudo ls", "echo x > /etc/gaia-probe"])
def test_granted_approval_lets_the_exact_command_run(command, approvals_test_db, monkeypatch, tmp_path):
    import gaia.approvals.store as astore

    db_path, _ = approvals_test_db
    monkeypatch.setattr("gaia.approvals.store._open_db", lambda: sqlite3.connect(str(db_path)))
    original = astore.get_pending

    def _pending(session_id=None, all_sessions=False, con=None):
        return original(session_id=session_id, all_sessions=all_sessions, con=con or sqlite3.connect(str(db_path)))

    monkeypatch.setattr("gaia.approvals.store.get_pending", _pending)
    _isolate_writer_db(monkeypatch, tmp_path)

    denied = _validate(command)
    reason = denied.block_response["hookSpecificOutput"]["permissionDecisionReason"]
    approval_id = re.search(r"approval_id:\s*(P-[\w-]+)", reason).group(1)
    assert activate_db_pending_by_id(approval_id, current_session_id=SESSION).success

    retried = _validate(command)
    assert retried.allowed, retried.reason
    assert retried.consumed_approval_id == approval_id
