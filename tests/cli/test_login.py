"""`gaia login` hands back a login link at once and keeps the login alive after it exits.

A stand-in `gcloud` on PATH plays the browser flow: it prints the link the real
CLI prints, waits for a "click" file, then records the account as credentialed.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

GAIA_BIN = Path(__file__).resolve().parents[2] / "bin" / "gaia"
ACCOUNT = "someone@example.com"

FAKE_GCLOUD = '''#!{python}
import os, sys, time
from pathlib import Path
state = Path(os.environ["FAKE_GCLOUD_STATE"])
args = sys.argv[1:]
if args[:2] == ["auth", "login"]:
    if os.environ.get("FAKE_GCLOUD_FLOW") == "paste":
        redirect = "https%3A%2F%2Fsdk.cloud.google.com%2Fauthcode.html"
    else:
        redirect = "http%3A%2F%2Flocalhost%3A8085%2F"
    print("Your browser has been opened to visit:\\n", file=sys.stderr)
    print("    https://accounts.google.com/o/oauth2/auth?response_type=code"
          "&redirect_uri=" + redirect + "&state=abc\\n", file=sys.stderr, flush=True)
    while not (state / "click").exists():
        time.sleep(0.05)
    if not os.environ.get("FAKE_GCLOUD_SKIP_ACCOUNT"):
        (state / "accounts").write_text(args[2] + "\\n")
    sys.exit(0)
if args[:2] == ["auth", "list"]:
    accounts = state / "accounts"
    if accounts.exists():
        print(accounts.read_text(), end="")
    sys.exit(0)
sys.exit(2)
'''


@pytest.fixture
def login_env(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    gcloud = fake_bin / "gcloud"
    gcloud.write_text(FAKE_GCLOUD.format(python=sys.executable))
    gcloud.chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    env = dict(os.environ)
    env.pop("GAIA_DB", None)
    env.update(
        GAIA_DATA_DIR=str(tmp_path / "data"),
        PATH=f"{fake_bin}{os.pathsep}{env['PATH']}",
        FAKE_GCLOUD_STATE=str(state),
    )
    started = []
    yield env, state, started
    for pid in started:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _gaia(env, *args):
    return subprocess.run(
        [sys.executable, str(GAIA_BIN), "login", *args],
        env=env, capture_output=True, text=True, timeout=60,
    )


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _state_of(env, login_id):
    result = _gaia(env, "status", login_id)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["state"]


def test_start_returns_the_link_and_the_login_outlives_it(login_env):
    env, state, started = login_env

    result = _gaia(env, "start", "gcloud", "--account", ACCOUNT)

    assert result.returncode == 0, result.stderr
    started_login = json.loads(result.stdout)
    started.append(started_login["pid"])
    assert started_login["url"].startswith("https://accounts.google.com/o/oauth2/auth?")
    assert "redirect_uri=http%3A%2F%2Flocalhost" in started_login["url"]
    assert _alive(started_login["pid"]), "the login died with the command that started it"
    assert os.getsid(started_login["pid"]) != os.getsid(0)
    assert _state_of(env, started_login["login_id"]) == "pending"

    (state / "click").touch()
    deadline = time.monotonic() + 15
    while _state_of(env, started_login["login_id"]) == "pending" and time.monotonic() < deadline:
        time.sleep(0.2)

    assert _state_of(env, started_login["login_id"]) == "complete"
    assert not _alive(started_login["pid"])


def _logins_dir(env):
    return Path(env["GAIA_DATA_DIR"]) / "state" / "logins"


def _wait_until_finished(env, login_id):
    deadline = time.monotonic() + 15
    while _state_of(env, login_id) == "pending" and time.monotonic() < deadline:
        time.sleep(0.2)
    return _state_of(env, login_id)


def test_login_files_are_owner_only(login_env):
    env, state, started = login_env

    started_login = json.loads(_gaia(env, "start", "gcloud", "--account", ACCOUNT).stdout)
    started.append(started_login["pid"])
    (state / "click").touch()
    _wait_until_finished(env, started_login["login_id"])

    logins = _logins_dir(env)
    assert logins.stat().st_mode & 0o777 == 0o700
    for suffix in (".json", ".log", ".result"):
        assert (logins / f"{started_login['login_id']}{suffix}").stat().st_mode & 0o777 == 0o600, suffix


def test_a_clean_exit_without_the_account_listed_is_not_complete(login_env):
    env, state, started = login_env
    env["FAKE_GCLOUD_SKIP_ACCOUNT"] = "1"

    started_login = json.loads(_gaia(env, "start", "gcloud", "--account", ACCOUNT).stdout)
    started.append(started_login["pid"])
    (state / "click").touch()

    assert _wait_until_finished(env, started_login["login_id"]) == "failed"


@pytest.mark.parametrize("pid", [os.getpid(), 1], ids=["reused-by-own-process", "owned-by-another-user"])
def test_a_pid_now_held_by_another_process_is_not_pending(login_env, pid):
    env, _state, _started = login_env
    logins = _logins_dir(env)
    logins.mkdir(parents=True)
    login_id = "0123456789ab"
    (logins / f"{login_id}.json").write_text(json.dumps({
        "login_id": login_id, "cli": "gcloud", "account": ACCOUNT, "pid": pid,
        "pid_start_time": "0", "log": str(logins / f"{login_id}.log"),
        "result": str(logins / f"{login_id}.result"), "started_at": 0,
    }))

    assert _state_of(env, login_id) == "failed"


def test_start_refuses_the_copy_code_flow_and_stops_it(login_env):
    env, _state, started = login_env
    env["FAKE_GCLOUD_FLOW"] = "paste"

    result = _gaia(env, "start", "gcloud", "--account", ACCOUNT)

    assert result.returncode == 1
    assert "copy" in result.stderr.lower()
    logins = Path(env["GAIA_DATA_DIR"]) / "state" / "logins"
    record = json.loads(next(logins.glob("*.json")).read_text())
    started.append(record["pid"])
    deadline = time.monotonic() + 5
    while _alive(record["pid"]) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(record["pid"])


def test_start_rejects_an_account_that_reads_as_a_flag(login_env):
    env, _state, _started = login_env

    result = _gaia(env, "start", "gcloud", "--account=--activate")

    assert result.returncode == 2
    assert "--account" in result.stderr
    assert not (Path(env["GAIA_DATA_DIR"]) / "state" / "logins").exists()
