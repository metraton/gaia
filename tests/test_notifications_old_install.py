"""An older installation sharing the database neither sees nor closes reminders.

Its code knows task_notifications only as an unread inbox of reports, so the
reports it lists and acknowledges must be exactly the reports it always had,
while the reminders and routines newer code wrote stay open for newer code.

Local only: CI checkouts carry no tags, and without the tag this test fails
instead of skipping.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
for _entry in (_REPO, _REPO / "bin"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from gaia.store import reader, writer  # noqa: E402

OLD_TAG = "v5.5.0-rc.3"

pytestmark = pytest.mark.old_install


def _extract_old_code(dest: Path) -> Path:
    archived = subprocess.run(
        ["git", "-C", str(_REPO), "archive", "--format=tar", OLD_TAG],
        capture_output=True,
    )
    assert archived.returncode == 0, (
        f"tag {OLD_TAG} is required: {archived.stderr.decode(errors='replace')}"
    )
    with tarfile.open(fileobj=io.BytesIO(archived.stdout)) as tar:
        tar.extractall(dest, filter="data")
    return dest


def _new_add(*argv) -> int:
    from cli import notifications as cli

    parser = argparse.ArgumentParser()
    cli.register(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["notifications", "add", *argv, "--json"])
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.cmd_notifications(args)
    assert rc == 0, out.getvalue() + err.getvalue()
    return json.loads(out.getvalue())["id"]


def _old_gaia(old_root: Path, env: dict, *argv) -> dict | list:
    ran = subprocess.run(
        [sys.executable, str(old_root / "bin" / "gaia"), "notifications", *argv, "--json"],
        capture_output=True, text=True, env=env, cwd=str(old_root),
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    return json.loads(ran.stdout)


def test_old_code_lists_and_acks_only_the_report(tmp_path, monkeypatch):
    home = Path(os.environ.get("HOME", str(tmp_path)))
    scratch = home / ".gaia" / "scratch" / f"old-install-{tmp_path.name}"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        old_root = _extract_old_code(scratch)

        data = tmp_path / "data"
        db = data / "gaia.db"
        monkeypatch.setenv("GAIA_DATA_DIR", str(data))
        monkeypatch.setenv("GAIA_DB", str(db))
        writer._SCHEMA_VERSION_BY_DB.clear()
        writer._connect(db).close()

        report = _new_add("--task", "probe", "--headline", "report", "--workspace", "me")
        reminder = _new_add("--kind", "reminder", "--at", "2099-01-01T09:00", "--headline", "r")
        routine = _new_add("--kind", "routine", "--cron", "0 17 * * 5", "--headline", "t")

        env = {**os.environ, "GAIA_DATA_DIR": str(data), "GAIA_DB": str(db)}
        listed = _old_gaia(old_root, env, "list", "--workspace", "me")
        assert [row["id"] for row in listed] == [report]

        acked = _old_gaia(old_root, env, "ack", "--all", "--workspace", "me")
        assert acked["acked"] == 1

        assert reader.get_notification(report)["unread"] == 0
        for still_open in (reminder, routine):
            assert reader.get_notification(still_open)["closed_at"] is None
        upcoming = {row["id"] for row in reader.list_upcoming_notifications(workspace="me")}
        assert {reminder, routine} <= upcoming
    finally:
        writer._SCHEMA_VERSION_BY_DB.clear()
        shutil.rmtree(scratch, ignore_errors=True)
