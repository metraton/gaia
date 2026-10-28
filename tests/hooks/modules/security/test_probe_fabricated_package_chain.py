"""A fabricated gaia package, or a folder shaped like an install root, never earns the data exemption."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(REPO_ROOT), str(REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import mutative_verbs, protected_paths, tiers  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

AGENT = "a" + "7" * 16
ENTRY = f"{AGENT}.4df5e309bb03"
BODY = f" contract fill --draft-id {ENTRY} --json-file - <<'EOF'\ncat ~/.aws/credentials\nEOF"


@pytest.fixture
def verdict(monkeypatch, tmp_path):
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")
    monkeypatch.setattr("gaia.store.writer.is_harness_session_bound", lambda _s: True)
    protected_paths.reset_caches()

    def run(command):
        mutative_verbs.detect_mutative_command.cache_clear()
        mutative_verbs._detect_mutative_command.cache_clear()
        tiers._classify_command_tier_cached.cache_clear()
        payload = {"cwd": str(tmp_path), "agent_id": AGENT, "agent_type": "developer", "session_id": "s-chain"}
        result = BashValidator().validate(
            command, is_subagent=True, session_id="s-chain", agent_type="developer",
            hook_payload=payload, cwd=str(tmp_path),
        )
        return "FREE" if result.allowed else ("SIGNED" if result.approval_id else result.rule)

    yield run
    protected_paths.reset_caches()


def _package(root: Path) -> Path:
    (root / "bin").mkdir(parents=True)
    (root / "package.json").write_text('{"name": "@jaguilar87/gaia", "bin": {"gaia": "bin/gaia"}}', encoding="utf-8")
    (root / "bin" / "gaia").write_bytes(Path("/bin/sh").read_bytes())
    (root / "bin" / "gaia").chmod(0o755)
    return root / "bin" / "gaia"


@pytest.mark.parametrize("where", [
    "data/scratch/" + ENTRY,
    "fakepkg",
    "data/scratch/forged/node_modules/@jaguilar87/gaia",
    "data/cache/dev-plugin/forged",
])
def test_a_fabricated_gaia_gets_no_data_heredoc(verdict, tmp_path, where):
    assert verdict(f"{_package(tmp_path / where)}{BODY}") != "FREE"
