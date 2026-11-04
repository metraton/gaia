"""A program that runs a command it is handed is judged by that command; one whose command cannot be found signs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security.tiers import SecurityTier  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

LAUNCHED = {
    "systemd-run": "systemd-run --user sqlite3 {S} 'VACUUM'",
    "parallel": "parallel sqlite3 {S} ::: VACUUM",
    "parallel-string": "parallel 'sqlite3 {{}} VACUUM' ::: {S}",
    "tmux": "tmux new-session -d sqlite3 {S} VACUUM",
    "tmux-string": "tmux new-session -d 'sqlite3 {S} VACUUM'",
    "runuser": "runuser -u jorge -- sqlite3 {S} 'VACUUM'",
    "bwrap": "bwrap --bind / / --chdir /tmp sqlite3 {S} 'VACUUM'",
    "make-stdin": "make -C /tmp -f /dev/stdin <<'EOF'\nall:\n\tsqlite3 {S} VACUUM\nEOF",
    "trap": "trap 'sqlite3 {S} VACUUM' EXIT",
    "coproc": "coproc sqlite3 {S} VACUUM",
    "dbus-run-session": "dbus-run-session -- sqlite3 {S} VACUUM",
    "renamed-copy": "/opt/tools/launch-it --quiet sqlite3 {S} VACUUM",
    "computed-payload": "systemd-run --user \"$RUN\" {S}",
    "lone-reboot": "xvfb-run reboot",
    "lone-poweroff": "xvfb-run poweroff",
    "lone-shutdown": "/opt/tools/bg-run shutdown",
    "lone-after-options": "xvfb-run -a --server-num 5 --auth-file /tmp/xa --error-file /tmp/xe reboot",
    "mapfile-callback": "mapfile -C \"$c\" lines < f",
    "readarray-callback": "readarray -C \"$c\" lines < f",
}

READ_ONLY = {
    "timeout": "timeout 5 ls /tmp",
    "chrt-ls": "chrt -f 10 ls /tmp",
    "launcher-ls": "systemd-run --user ls /tmp",
    "make-target": "make -C /tmp test",
    "trap-echo": "trap 'echo bye' EXIT",
    "program-mention": "ldd /usr/bin/ls",
    "lone-read-only": "xvfb-run ls",
}


@pytest.fixture()
def store(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(data / "gaia.db"))
    from gaia.paths import ensure_layout
    from gaia.store.writer import insert_dispatched_handoff

    ensure_layout()
    insert_dispatched_handoff("a1107.launch", "a1107", "me", session_id="ses-1107", agent_name="gaia-system")
    return data / "gaia.db"


def _verdict(command):
    return BashValidator().validate(
        command, is_subagent=True, session_id="ses-1107", agent_type="gaia-system", cwd="/tmp",
    )


def test_payload_alone_signs(store):
    verdict = _verdict(f"sqlite3 {store} 'VACUUM'")
    assert not verdict.allowed and verdict.rule != "floor.approval-store", verdict.reason


@pytest.mark.parametrize("name", list(LAUNCHED))
def test_launched_payload_is_judged(name, store):
    verdict = _verdict(LAUNCHED[name].format(S=store))
    assert not verdict.allowed and verdict.rule != "floor.approval-store", verdict.reason


@pytest.mark.parametrize("name", list(READ_ONLY))
def test_read_only_payload_stays_free(name, store):
    verdict = _verdict(READ_ONLY[name])
    assert verdict.allowed and verdict.tier == SecurityTier.T0_READ_ONLY, verdict.reason
