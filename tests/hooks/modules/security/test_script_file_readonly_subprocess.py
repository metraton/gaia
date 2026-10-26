"""A Python script file is judged by what its subprocess argv and HTTP calls do.

The measured helper fetched a token with ``gcloud auth print-access-token`` and
issued GET requests, yet every run with new arguments asked for a signature.
These rows pin the free verdict for that shape, the T3 verdict for each twin
that does mutate or cannot be read statically, and the ``cp`` into scratch the
same session paid for.
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
HOOKS_DIR = REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(REPO_ROOT))

from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.security.tiers import SecurityTier  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

HELPER_SHAPE = '''\
import argparse
import json
import subprocess
import urllib.parse
import urllib.request

BASE = "https://monitoring.googleapis.com/v1/projects/p/location/global/prometheus/api/v1"


def fetch_token():
    result = subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def api(path, params):
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {fetch_token()}"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.load(resp)


parser = argparse.ArgumentParser()
parser.add_argument("--window", action="append", default=[])
print(api("query_range", {"query": "up", "windows": parser.parse_args().windows}))
'''

TOKEN_CALL = 'subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True, check=True)'

ASKING_TWINS = {
    "gcloud-delete-argv": HELPER_SHAPE.replace(
        TOKEN_CALL,
        'subprocess.run(["gcloud", "compute", "instances", "delete", "vm-1"], capture_output=True, text=True, check=True)',
    ),
    "kubectl-apply-argv": HELPER_SHAPE.replace(
        TOKEN_CALL,
        'subprocess.run(["kubectl", "apply", "-f", "deploy.yaml"], capture_output=True, text=True, check=True)',
    ),
    "argv-from-variable": HELPER_SHAPE.replace(
        TOKEN_CALL,
        'subprocess.run(ARGV, capture_output=True, text=True, check=True)',
    ).replace("BASE = ", 'ARGV = ["gcloud", "auth", "print-access-token"]\nBASE = '),
    "argv-element-from-variable": HELPER_SHAPE.replace(
        TOKEN_CALL,
        'subprocess.run(["gcloud", "auth", VERB], capture_output=True, text=True, check=True)',
    ).replace("BASE = ", 'VERB = "print-access-token"\nBASE = '),
    "shell-true": HELPER_SHAPE.replace(
        TOKEN_CALL,
        'subprocess.run("gcloud auth print-access-token", shell=True, capture_output=True, text=True, check=True)',
    ),
    "shell-true-on-literal-argv": HELPER_SHAPE.replace(
        TOKEN_CALL,
        'subprocess.run(["gcloud", "auth", "print-access-token"], shell=True, capture_output=True, text=True)',
    ),
    "stdin-fed-shell": HELPER_SHAPE.replace(
        TOKEN_CALL,
        'subprocess.run(["sh"], input="gcloud projects delete p", capture_output=True, text=True)',
    ),
    "mutating-file-api": HELPER_SHAPE.replace(
        "print(api(", 'open("/tmp/usage.csv", "w").write("x")\nprint(api(',
    ),
    "http-post-data": HELPER_SHAPE.replace(
        'headers={"Authorization"', 'data=b"{}", headers={"Authorization"',
    ),
    "http-method-delete": HELPER_SHAPE.replace(
        'headers={"Authorization"', 'method="DELETE", headers={"Authorization"',
    ),
    "http-urlopen-data-argument": HELPER_SHAPE.replace(
        "urlopen(req, timeout=300)", 'urlopen(req, b"payload", 300)',
    ),
    "http-method-set-after-construction": HELPER_SHAPE.replace(
        "    with urllib.request.urlopen", '    req.method = "PUT"\n    with urllib.request.urlopen',
    ),
    "http-request-from-partial": HELPER_SHAPE.replace(
        "req = urllib.request.Request(url, ", 'req = functools.partial(urllib.request.Request, data=b"x")(url, ',
    ).replace("import json", "import functools\nimport json"),
    "http-request-data-by-setattr": HELPER_SHAPE.replace(
        "    with urllib.request.urlopen", '    setattr(req, "data", b"x")\n    with urllib.request.urlopen',
    ),
    "http-request-from-unread-helper": HELPER_SHAPE.replace(
        "req = urllib.request.Request(url, ", "req = make_request(url, ",
    ).replace("import json", "from client import make_request\nimport json"),
}

for _program in (["sh"], ["bash", "-s"], ["python3"], ["node"]):
    ASKING_TWINS["interpreter-without-program-" + "-".join(_program)] = HELPER_SHAPE.replace(
        TOKEN_CALL, f"subprocess.run({_program!r}, capture_output=True, text=True, check=True)",
    )

for _keyword in ('env={"PATH": "/tmp/bin"}', 'cwd="/tmp/elsewhere"', 'executable="/tmp/bin/gcloud"'):
    ASKING_TWINS["keyword-" + _keyword.split("=", 1)[0]] = HELPER_SHAPE.replace(
        "capture_output=True, text=True, check=True)", f"capture_output=True, text=True, check=True, {_keyword})",
    )


@pytest.fixture
def scratch(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    from gaia.paths import ensure_layout, scratch_dir
    ensure_layout()
    detect_mutative_command.cache_clear()
    return Path(os.path.realpath(str(scratch_dir())))


@pytest.fixture
def validator():
    return BashValidator()


def _write(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(body)
    return path


def _validate(validator, command):
    detect_mutative_command.cache_clear()
    return validator.validate(command)


@pytest.mark.parametrize("arguments", [
    "--window call_0928=2026-09-28T16:24:00Z/2026-09-28T16:34:00Z",
    "--window idle_1004=2026-10-04T16:00:00Z/2026-10-04T16:10:00Z --window b=x/y",
])
def test_helper_shape_runs_free_with_any_arguments(validator, scratch, arguments):
    script = _write(scratch, "usage.py", HELPER_SHAPE)

    verdict = _validate(validator, f"python3 {script} {arguments}")

    assert verdict.allowed, verdict.reason
    assert verdict.tier == SecurityTier.T0_READ_ONLY
    assert verdict.block_response is None


@pytest.mark.parametrize("twin", sorted(ASKING_TWINS))
def test_twin_that_mutates_or_hides_its_argv_still_asks(validator, scratch, twin):
    script = _write(scratch, f"{twin}.py", ASKING_TWINS[twin])

    assert detect_mutative_command(f"python3 {script}").is_mutative
    verdict = _validate(validator, f"python3 {script} --window a=x/y")
    assert not verdict.allowed
    assert verdict.tier == SecurityTier.T3_BLOCKED


def test_cp_into_scratch_runs_free(validator, scratch, tmp_path):
    source = _write(tmp_path, "helper.py", HELPER_SHAPE)
    entry = scratch / "aentryowner01.token"
    entry.mkdir()

    for destination in (entry / "helper.py", entry):
        detect_mutative_command.cache_clear()
        verdict = validator.validate(
            f"cp {source} {destination}", hook_payload={"agent_id": "aentryowner01", "cwd": str(tmp_path)},
        )
        assert verdict.allowed, verdict.reason
        assert verdict.tier == SecurityTier.T0_READ_ONLY


@pytest.mark.parametrize("command", [
    "cp {source} {outside}/copy.py",
    "cp {source} {scratch}/../escaped.py",
    "cp {scratch}/a.py {outside}",
    "cp -s {source} {scratch}/link.py",
    "cp -l {source} {scratch}/hard.py",
    "cp -t {outside} {source}",
    "cp {source} {scratch}/*",
    "cp -T {source} {scratch}/copy.py",
    "cp -x {source} {scratch}/copy.py",
    "cp --sparse=always {source} {scratch}/copy.py",
])
def test_cp_that_can_land_outside_scratch_keeps_its_verdict(validator, scratch, tmp_path, command):
    source = _write(tmp_path, "helper.py", HELPER_SHAPE)
    outside = tmp_path / "outside"
    outside.mkdir()
    rendered = command.format(source=source, scratch=scratch, outside=outside)

    assert detect_mutative_command(rendered).is_mutative
    assert not _validate(validator, rendered).allowed


@pytest.mark.parametrize("flag", ["-r", "-R", "-a", "--recursive", "--archive"])
def test_recursive_cp_through_a_nested_symlink_in_scratch_asks(validator, scratch, tmp_path, flag):
    outside = tmp_path / "outside"
    outside.mkdir()
    (scratch / "tree").mkdir()
    (scratch / "tree" / "sub").symlink_to(outside)
    source = tmp_path / "src" / "tree"
    (source / "sub").mkdir(parents=True)
    (source / "sub" / "payload.txt").write_text("x")
    command = f"cp {flag} {source} {scratch}"

    assert detect_mutative_command(command).is_mutative
    assert not _validate(validator, command).allowed


def test_cp_over_a_durable_store_in_scratch_keeps_its_verdict(scratch, tmp_path):
    from modules.security.mutative_verbs import _DURABLE_STORE_MIN_BYTES
    store = scratch / "live_copy.db"
    with store.open("wb") as handle:
        handle.write(b"SQLite format 3\x00")
        handle.truncate(_DURABLE_STORE_MIN_BYTES * 2)
    source = _write(tmp_path, "helper.py", HELPER_SHAPE)

    assert detect_mutative_command(f"cp {source} {store}").is_mutative
