"""
Evidence deposit: a rejected attempt leaves no orphan blob (AC-1, adversarial).

Brief: lo-que-gaia-crea-gaia-lo-limpia-evidencia-copiada-scratch-ensenado-
retencion-por-estado.

``bin/cli/evidence.py::_cmd_add`` writes the filesystem blob (via
``gaia.evidence.fs.write_blob``) BEFORE calling ``insert_evidence``, which is
where the permission guard and the row-level validation run. Without ordering
protection, any rejection inside ``insert_evidence`` -- for any reason --
would leave the just-written blob orphaned on disk with no DB row.

This is a property test, not a case list: for every distinct rejection the
guard/validation path exposes (derived from ``gaia/evidence/store.py`` and
``bin/cli/evidence.py``, not enumerated by a spec), a rejected deposit must
leave the canonical evidence root's file set byte-for-byte identical
(same paths, same sizes, no new file) to what it was before the attempt.

Three distinct rejection reasons are exercised, each reachable through
``_cmd_add`` after the blob would already have been written:

  1. EvidenceWriteForbidden -- GAIA_DISPATCH_AGENT set to an identity that is
     neither a curator nor the admitted producer (e.g. "not-an-agent").
  2. ValueError (invalid evidence type) -- ``_cmd_add`` does not itself
     validate ``--type`` against ``_VALID_EVIDENCE_TYPES`` (only argparse's
     ``choices`` does, which a hand-built ``argparse.Namespace`` bypasses,
     exactly as the sibling red test in this same directory does); the
     blob-vs-inline branching in ``_cmd_add`` never inspects ``ev_type``
     either, so the blob write happens before ``insert_evidence`` rejects
     the bad type.
  3. ValueError (ac_id cannot be empty) -- ``_cmd_add`` passes ``--ac``
     straight into ``write_blob`` as a path component before
     ``insert_evidence`` validates it is non-empty.

If the guard/validation path is ever extended with a genuinely new rejection
reason, this suite should grow a matching case; a single case is deliberately
insufficient coverage for a plural claim ("por cualquier motivo").
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))


def _seed_brief(workspace: str = "me", name: str = "brief-evidence-reject-test") -> None:
    """Create the parent brief row the deposit's --brief lookup needs.

    Runs with no GAIA_DISPATCH_AGENT set (autouse fixtures clear it before
    each test), so this is a human-caller write and unconditionally
    permitted -- it is not part of what this test exercises.
    """
    from gaia.briefs.store import upsert_brief
    upsert_brief(workspace, name, {"title": "seed", "objective": "seed"})


def _snapshot_evidence_root() -> set[tuple[str, int]]:
    """Return {(relative_path, size_bytes)} for every file under the
    canonical evidence root. The isolated per-test GAIA_DATA_DIR (autouse
    fixture in tests/conftest.py) makes this root a throwaway tmp directory,
    never the real per-user store."""
    from gaia.paths import evidence_dir

    root = evidence_dir()
    if not root.exists():
        return set()
    return {
        (str(p.relative_to(root)), p.stat().st_size)
        for p in root.rglob("*")
        if p.is_file()
    }


def _base_args(**overrides) -> argparse.Namespace:
    defaults = dict(
        brief="brief-evidence-reject-test",
        ac="AC-1",
        type="file",
        text=None,
        artifact_file=None,
        task=None,
        created_by="developer",
        workspace="me",
        json=True,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _make_source_file(tmp_path: Path) -> Path:
    """Binary and > EVIDENCE_INLINE_MAX_BYTES (4096) so the deposit always
    takes the filesystem-blob path rather than storing inline in gaia.db --
    the exact condition under which an orphan blob is possible."""
    source = tmp_path / f"payload-{uuid.uuid4()}.bin"
    source.write_bytes(bytes(range(256)) * 20)  # 5120 bytes
    return source


@pytest.mark.parametrize(
    "case_name, env_agent, arg_overrides",
    [
        (
            # Outside the declared fleet: producers are derived as
            # fleet-minus-curators, so an unregistered name is the identity
            # the write guard still refuses.
            "identity_not_curator_or_producer",
            "not-an-agent",
            {},
        ),
        (
            "invalid_evidence_type",
            "developer",
            {"type": "not-a-real-evidence-type"},
        ),
        (
            "empty_ac_id",
            "developer",
            {"ac": ""},
        ),
    ],
)
def test_rejected_deposit_leaves_no_orphan_file(
    tmp_path, monkeypatch, capsys, case_name, env_agent, arg_overrides,
):
    """For each distinct rejection reason, the evidence root's file set is
    unchanged (same paths, same sizes, no new file) after the rejected
    attempt."""
    from cli import evidence as cli_evidence

    _seed_brief()

    source = _make_source_file(tmp_path)
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", env_agent)

    args = _base_args(artifact_file=str(source), **arg_overrides)

    before = _snapshot_evidence_root()
    rc = cli_evidence._cmd_add(args)
    capsys.readouterr()  # drain, not asserted on -- the property is the FS state
    after = _snapshot_evidence_root()

    assert rc != 0, (
        f"[{case_name}] expected the deposit to be rejected (rc != 0), got "
        f"rc={rc} -- this case no longer exercises a rejection at all."
    )
    assert after == before, (
        f"[{case_name}] a rejected deposit left the evidence root changed: "
        f"before={before!r} after={after!r}"
    )
