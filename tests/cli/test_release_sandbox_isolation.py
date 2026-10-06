"""The sandbox install gate gives bin/validate-sandbox.sh its own data home:
a GAIA_DB or GAIA_DATA_DIR exported in the release operator's shell never
reaches the harness, so the sandbox cannot seed the operator's database.

The harness here is a fake script that writes to whatever GAIA_DB it
inherits, as the real one's gaia calls would; it is run through the real
`gate_npm_sandbox` subprocess path.
"""

import sys
from pathlib import Path

_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli import release  # noqa: E402

_FAKE_HARNESS = (
    'printf "%s|%s" "${GAIA_DB-unset}" "${GAIA_DATA_DIR-unset}" > "$SANDBOX_PROBE"\n'
    'if [ -n "${GAIA_DB:-}" ]; then echo seeded >> "$GAIA_DB"; fi\n'
)


def test_inherited_gaia_db_and_data_dir_do_not_reach_the_sandbox_harness(tmp_path, monkeypatch):
    """A release run with GAIA_DB and GAIA_DATA_DIR exported leaves that database untouched."""
    user_db = tmp_path / "user.db"
    user_db.write_bytes(b"user state")
    probe = tmp_path / "probe"
    monkeypatch.setenv("GAIA_DB", str(user_db))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "user-data"))
    monkeypatch.setenv("SANDBOX_PROBE", str(probe))
    repo = tmp_path / "repo"
    (repo / "bin").mkdir(parents=True)
    (repo / "bin" / "validate-sandbox.sh").write_text(_FAKE_HARNESS)
    pack = {"action": "created", "tarball": tmp_path / "gaia.tgz"}

    result = release.gate_npm_sandbox(repo, pack)

    assert result["status"] == "PASS", result
    assert user_db.read_bytes() == b"user state"
    assert probe.read_text() == "unset|unset"
