"""The marketplace gate, run as release check runs it, passes on a package of this tree."""

import sys
import tarfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "bin") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "bin"))

from cli import release  # noqa: E402

_PACKED = ("hooks", "gaia", "bin", "config", "opencode", "agents", "skills", "package.json")


def _skip_caches(member: tarfile.TarInfo):
    return None if "__pycache__" in member.name else member


def _package(tmp_path: Path) -> Path:
    tarball = tmp_path / "gaia.tgz"
    with tarfile.open(tarball, "w:gz") as archive:
        for name in _PACKED:
            archive.add(_REPO_ROOT / name, arcname=f"package/{name}", filter=_skip_caches)
    return tarball


def test_the_gate_passes_on_a_package_of_this_tree(tmp_path):
    result = release.gate_marketplace_identity({"action": "created", "tarball": _package(tmp_path), "version": "5.7.1"})

    assert result["status"] == "PASS", result["detail"]
