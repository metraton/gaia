"""The Python script and -c lanes judge a process call however it is reached and however the source is encoded."""
import os
import shlex
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "hooks"))
sys.path.insert(0, str(REPO_ROOT))

from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.security.tiers import SecurityTier  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

PUSH = '["git", "push", "origin", "HEAD"]'
REACHED_PROCESS_CALLS = {
    "rebound-name": f"import subprocess\nr = subprocess.run\nr({PUSH})\n",
    "print-rebound": f"import subprocess\nprint = subprocess.run\nprint({PUSH})\n",
    "partial": f"import functools\nimport subprocess\nfunctools.partial(subprocess.run, {PUSH})()\n",
    "star-import": f"from subprocess import *\nrun({PUSH})\n",
}

UTF7_HIDDEN = {
    "hidden-assignment": (
        b'# -*- coding: utf-7 -*-\nimport subprocess\nWT = "/repo/worktree"\nOP = "status"\n'
        b'# +AAo-OP = "push"\nsubprocess.run(["git", "-C", WT, OP, "origin", "HEAD"])\n'
    ),
    "hidden-call": (
        b'# -*- coding: utf-7 -*-\nimport subprocess\n'
        b'# +AAo-subprocess.run(+AFs-"git", "push", "origin", "HEAD"+AF0-)\nsubprocess.run(["git", "status"])\n'
    ),
}
BOM = b"\xef\xbb\xbf"
STATUS = b'import subprocess\nprint(subprocess.run(["git", "status"], capture_output=True).stdout)\n'


@pytest.fixture
def scratch(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    from gaia.paths import ensure_layout, scratch_dir
    ensure_layout()
    return Path(os.path.realpath(str(scratch_dir())))


def _verdict(command):
    detect_mutative_command.cache_clear()
    return BashValidator(agent_names_scratch=True).validate(command)


def _script(scratch, body: bytes):
    path = scratch / "probe.py"
    path.write_bytes(body)
    return _verdict(f"python3 {path}")


@pytest.mark.parametrize("shape", sorted(REACHED_PROCESS_CALLS))
def test_process_call_reached_through_a_binding_asks_in_a_script(scratch, shape):
    verdict = _script(scratch, REACHED_PROCESS_CALLS[shape].encode())

    assert not verdict.allowed and verdict.tier == SecurityTier.T3_BLOCKED, verdict.reason


@pytest.mark.parametrize("shape", sorted(REACHED_PROCESS_CALLS))
def test_process_call_reached_through_a_binding_asks_inline(scratch, shape):
    verdict = _verdict("python3 -c " + shlex.quote(REACHED_PROCESS_CALLS[shape]))

    assert not verdict.allowed and verdict.tier == SecurityTier.T3_BLOCKED, verdict.reason


@pytest.mark.parametrize("shape", sorted(UTF7_HIDDEN))
def test_line_hidden_by_a_coding_cookie_is_judged_as_python_decodes_it(scratch, shape):
    verdict = _script(scratch, UTF7_HIDDEN[shape])

    assert not verdict.allowed and verdict.tier == SecurityTier.T3_BLOCKED, verdict.reason


def test_bom_source_is_judged_by_its_decoded_text(scratch):
    assert _script(scratch, BOM + STATUS).tier == SecurityTier.T0_READ_ONLY
    pushes = _script(scratch, BOM + STATUS.replace(b'"status"', b'"push", "origin", "HEAD"'))
    assert not pushes.allowed and pushes.tier == SecurityTier.T3_BLOCKED, pushes.reason


def test_ascii_compatible_cookie_is_decoded_not_refused(scratch):
    verdict = _script(scratch, b"# -*- coding: latin-1 -*-\n" + STATUS)

    assert verdict.allowed and verdict.tier == SecurityTier.T0_READ_ONLY, verdict.reason


@pytest.mark.parametrize("body", [
    b"# coding: no-such-codec\n" + STATUS,
    b"# coding: latin-1\n# caf\xe9\n" + STATUS,
], ids=["unknown-codec", "bytes-lost-by-the-reader"])
def test_source_that_cannot_be_decoded_as_python_does_signs(scratch, body):
    verdict = _script(scratch, body)

    assert not verdict.allowed and verdict.tier == SecurityTier.T3_BLOCKED, verdict.reason
