"""The working-tree Bash validator against the merge-base one, held to tools/validator_replay/caps.json; runs only under -m replay."""

from __future__ import annotations

import os
import pwd
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.validator_replay import replay  # noqa: E402

HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)


@pytest.mark.replay
def test_the_working_tree_validator_stays_within_the_friction_caps(request):
    if "replay" not in (request.config.getoption("markexpr") or ""):
        pytest.skip("the replay reads real history and runs minutes; select it with -m replay")
    out = Path(os.environ.get("GAIA_REPLAY_OUT") or HOME / ".gaia" / "scratch" / f"validator-replay-{time.strftime('%Y%m%dT%H%M%S')}")
    result = replay.run(caps=REPO / "tools" / "validator_replay" / "caps.json", out=out,
                        db=Path(os.environ.get("GAIA_REPLAY_DB") or HOME / ".gaia" / "gaia.db"),
                        declared=os.environ.get("GAIA_REPLAY_DECLARED"), since=os.environ.get("GAIA_REPLAY_SINCE"),
                        old=os.environ.get("GAIA_REPLAY_OLD"))
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    summary = f"replay {result['old']} -> working tree: sizes {result['sizes']}, {len(result['changes'])} changes, report {out / 'report.md'}"
    if reporter:
        reporter.write_line(summary)
    assert not result["violations"], f"{result['violations']}; {summary}"
