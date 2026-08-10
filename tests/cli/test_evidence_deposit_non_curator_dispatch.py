"""
Evidence deposit from a non-curator specialist dispatch (AC-1, red test).

Brief: lo-que-gaia-crea-gaia-lo-limpia-evidencia-copiada-scratch-ensenado-
retencion-por-estado.

A specialist dispatch (e.g. ``developer``, never ``orchestrator`` /
``operator``) must be able to deposit evidence for its own acceptance
criterion: ``gaia evidence add --artifact-file <path>`` should exit 0, mint a
blob under the canonical evidence root with a uuid4-shaped name, and preserve
the source file's exact size.

As of this commit it cannot: ``gaia.evidence.store._assert_dispatch_can_write_
evidence`` rejects every ``GAIA_DISPATCH_AGENT`` identity outside the closed
curator set (``orchestrator``, ``operator``, ``gaia-orchestrator``,
``gaia-operator``), so a specialist's deposit is refused with
``EvidenceWriteForbidden`` before the CLI can report success. This test is
EXPECTED TO FAIL against the current guard -- relaxing the guard for a
legitimate specialist deposit is a later task; do not edit the guard to make
this test pass.
"""

from __future__ import annotations

import argparse
import json
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


def _seed_brief(workspace: str = "me", name: str = "brief-evidence-deposit-test") -> None:
    """Create the parent brief row the deposit's --brief lookup needs.

    Runs with no GAIA_DISPATCH_AGENT set (the autouse _isolate_dispatch_identity
    fixture clears it), so this is a human-caller write and is unconditionally
    permitted -- it is not part of what this test exercises.
    """
    from gaia.briefs.store import upsert_brief
    upsert_brief(workspace, name, {"title": "seed", "objective": "seed"})


def test_evidence_deposit_by_non_curator_specialist_should_succeed(
    tmp_path, monkeypatch, capsys,
):
    """A specialist's evidence deposit must exit 0, land under the canonical
    evidence root, mint a uuid4-named blob, and preserve the source size.
    """
    from cli import evidence as cli_evidence
    from gaia.paths import evidence_dir

    _seed_brief()

    source = tmp_path / "source_payload.bin"
    # Binary and > EVIDENCE_INLINE_MAX_BYTES (4096) so the deposit always
    # takes the filesystem-blob path rather than storing inline in gaia.db.
    payload = bytes(range(256)) * 20  # 5120 bytes
    source.write_bytes(payload)

    # Specialist dispatch identity -- not in the curator set.
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")

    args = argparse.Namespace(
        brief="brief-evidence-deposit-test",
        ac="AC-1",
        type="file",
        text=None,
        artifact_file=str(source),
        task=None,
        created_by="developer",
        workspace="me",
        json=True,
    )

    rc = cli_evidence._cmd_add(args)
    captured = capsys.readouterr()

    assert rc == 0, (
        "Expected the specialist's evidence deposit to succeed (exit 0); the "
        "store's curator guard rejected it instead. Literal CLI output -- "
        f"stdout={captured.out!r} stderr={captured.err!r}"
    )

    result = json.loads(captured.out)
    artifact_path = Path(result["artifact_path"]).resolve()
    root = evidence_dir().resolve()

    # (2) path under the canonical evidence root
    artifact_path.relative_to(root)

    # (3) blob filename stem is uuid4-shaped
    uuid.UUID(artifact_path.stem)

    # (4) size identical to the source file
    assert result["size_bytes"] == source.stat().st_size
