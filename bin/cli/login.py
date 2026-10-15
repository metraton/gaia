"""
gaia login -- Start a CLI login detached from the agent turn and report whether it completed.

Subcommands:
  login start gcloud --account <email>   Run `gcloud auth login <email> --no-activate`
                                         in its own session; print its link as JSON
  login status <login_id>                Print pending, complete or failed as JSON

The login runs under a supervisor in a new session, so it outlives the turn that
started it. The user opens the link in a browser and gcloud receives the answer
on its localhost redirect; the active account is never switched.
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

LOGIN_TIMEOUT_SECONDS = 600
LINK_WAIT_SECONDS = 20
_ACCOUNT_RE = re.compile(r"^[^\s@-][^\s@]*@[^\s@]+$")
_LOGIN_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_GOOGLE_AUTH_URL_RE = re.compile(r"https://accounts\.google\.com/\S+")


def _logins_dir() -> Path:
    from gaia.paths import state_dir
    return state_dir() / "logins"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _redirects_to_localhost(url: str) -> bool:
    redirect = parse_qs(urlparse(url).query).get("redirect_uri", [""])[0]
    return redirect.startswith("http://localhost")


def _wait_for_link(log_path: Path, supervisor: subprocess.Popen):
    """Return the first Google login URL the log shows, or None if none comes."""
    deadline = time.monotonic() + LINK_WAIT_SECONDS
    while time.monotonic() < deadline:
        exited = supervisor.poll() is not None
        if log_path.exists():
            match = _GOOGLE_AUTH_URL_RE.search(log_path.read_text(errors="replace"))
            if match:
                return match.group(0)
        if exited:
            return None
        time.sleep(0.1)
    return None


def _stop(supervisor: subprocess.Popen) -> None:
    try:
        os.killpg(supervisor.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    supervisor.wait(timeout=5)


def _credentialed(account: str) -> bool:
    listed = subprocess.run(
        ["gcloud", "auth", "list", f"--filter=account:{account}", "--format=value(account)"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
    )
    return account in listed.stdout.split()


def _cmd_start(args) -> int:
    if not _ACCOUNT_RE.match(args.account):
        print(f"gaia login: --account must be an email address, got {args.account!r}", file=sys.stderr)
        return 2
    logins = _logins_dir()
    logins.mkdir(parents=True, exist_ok=True)
    login_id = uuid.uuid4().hex[:12]
    log_path = logins / f"{login_id}.log"
    result_path = logins / f"{login_id}.result"
    command = ["gcloud", "auth", "login", args.account, "--no-activate"]
    supervisor = subprocess.Popen(
        [sys.executable, "-I", str(Path(__file__).resolve()),
         "--supervise", str(log_path), str(result_path), "--", *command],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    record = {
        "login_id": login_id,
        "cli": args.cli,
        "account": args.account,
        "pid": supervisor.pid,
        "log": str(log_path),
        "result": str(result_path),
        "started_at": time.time(),
    }
    (logins / f"{login_id}.json").write_text(json.dumps(record))

    url = _wait_for_link(log_path, supervisor)
    if url is None:
        _stop(supervisor)
        print(f"gaia login: gcloud printed no login link within {LINK_WAIT_SECONDS}s; see {log_path}", file=sys.stderr)
        return 1
    if not _redirects_to_localhost(url):
        _stop(supervisor)
        print(
            "gaia login: gcloud fell back to the copy-code flow, which waits for a code "
            "pasted into a terminal; the login was stopped",
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"login_id": login_id, "account": args.account, "url": url, "pid": supervisor.pid}))
    return 0


def _cmd_status(args) -> int:
    record_path = _logins_dir() / f"{args.login_id}.json"
    if not _LOGIN_ID_RE.match(args.login_id) or not record_path.exists():
        print(f"gaia login: no login {args.login_id!r}", file=sys.stderr)
        return 1
    record = json.loads(record_path.read_text())
    result_path = Path(record["result"])
    if _alive(record["pid"]):
        state = "pending"
    elif result_path.exists() and json.loads(result_path.read_text())["returncode"] == 0 \
            and _credentialed(record["account"]):
        state = "complete"
    else:
        state = "failed"
    print(json.dumps({"login_id": args.login_id, "account": record["account"], "state": state}))
    return 0


def _supervise(log_path: str, result_path: str, command: list) -> None:
    """Run the login to completion or timeout and record how it ended."""
    # gcloud takes the localhost-redirect flow only when it believes a browser
    # opened; `true` opens nothing, so the link reaches the user through chat
    # whether or not WSL can start a Windows browser.
    env = dict(os.environ, BROWSER="true")
    timed_out = False
    returncode = None
    with open(log_path, "w") as log:
        try:
            returncode = subprocess.run(
                command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                env=env, timeout=LOGIN_TIMEOUT_SECONDS,
            ).returncode
        except subprocess.TimeoutExpired:
            timed_out = True
    Path(result_path).write_text(json.dumps({"returncode": returncode, "timed_out": timed_out}))


def cmd_login(args) -> int:
    """Top-level dispatcher for `gaia login <action>`."""
    return args.func(args)


def register(subparsers):
    """Register the login subcommand with its start and status actions."""
    login_parser = subparsers.add_parser(
        "login",
        help="Start a CLI login detached from the turn and check whether it completed",
        description=(
            "Start a CLI login that needs no terminal and survives the agent turn.\n"
            "start: runs the login in its own session and prints its link as JSON.\n"
            "status: prints pending, complete or failed as JSON."
        ),
    )
    actions = login_parser.add_subparsers(dest="login_action", metavar="<action>", required=True)

    start_p = actions.add_parser("start", help="Start a login and print its link")
    start_p.add_argument("cli", choices=["gcloud"], help="CLI to log in")
    start_p.add_argument("--account", required=True, help="Account email to add without activating it")
    start_p.set_defaults(func=_cmd_start)

    status_p = actions.add_parser("status", help="Report whether a started login completed")
    status_p.add_argument("login_id", help="Id printed by `gaia login start`")
    status_p.set_defaults(func=_cmd_status)


if __name__ == "__main__" and sys.argv[1:2] == ["--supervise"]:
    separator = sys.argv.index("--")
    _supervise(sys.argv[2], sys.argv[3], sys.argv[separator + 1:])
