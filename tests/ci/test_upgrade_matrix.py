"""The CI upgrade matrix covers every committed base and reaches the verdict."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CI = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
JOBS = CI["jobs"]


def test_every_committed_base_dump_has_a_cell_on_both_platforms():
    dumps = sorted(int(p.stem[1:]) for p in (ROOT / "tests" / "fixtures" / "published_bases").glob("v*.sql"))
    matrix = JOBS["upgrade"]["strategy"]["matrix"]
    assert sorted(matrix["base"]) == dumps
    assert set(matrix["os"]) == {"ubuntu-latest", "windows-latest"}
    assert JOBS["upgrade"]["strategy"]["fail-fast"] is False


def test_upgrade_is_skipped_exactly_when_the_shards_are():
    assert JOBS["upgrade"]["if"] == JOBS["test-python"]["if"]
    assert "decide" in JOBS["upgrade"]["needs"]


def test_a_failed_pack_or_cell_fails_the_verdict():
    needs = JOBS["ci-verdict"]["needs"]
    assert "upgrade" in needs and "pack" in needs
    assert "pack" in JOBS["upgrade"]["needs"]
