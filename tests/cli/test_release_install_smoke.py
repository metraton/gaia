"""The install smoke, run as release check runs it, passes on a package of this tree on every channel."""

import json
import sys
import tarfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "bin") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "bin"))

from cli import release  # noqa: E402

_MANIFEST = json.loads((_REPO_ROOT / "package.json").read_text(encoding="utf-8"))


def _skip_caches(member: tarfile.TarInfo):
    return None if "__pycache__" in member.name else member


def _package(tmp_path: Path, extra: dict[str, str] | None = None) -> Path:
    tarball = tmp_path / "gaia.tgz"
    with tarfile.open(tarball, "w:gz") as archive:
        for name in ["package.json", *(entry.rstrip("/") for entry in _MANIFEST["files"])]:
            if (_REPO_ROOT / name).exists():
                archive.add(_REPO_ROOT / name, arcname=f"package/{name}", filter=_skip_caches)
        for name, text in (extra or {}).items():
            source = tmp_path / Path(name).name
            source.write_text(text, encoding="utf-8")
            archive.add(source, arcname=f"package/{name}")
    return tarball


def _gate(tarball: Path) -> dict:
    return release.gate_install_smoke({"action": "created", "tarball": tarball, "version": _MANIFEST["version"]})


def test_every_channel_passes_the_session_smoke_set(tmp_path):
    result = _gate(_package(tmp_path))

    assert result["status"] == "PASS", result["detail"]
    for channel in ("marketplace", "npm-local", "npm-global", "dev-plugin"):
        assert channel in result["detail"]


def test_a_cli_that_warns_on_version_fails_the_gate(tmp_path):
    result = _gate(_package(tmp_path, {"bin/cli/zz_broken.py": "raise RuntimeError('broken plugin')\n"}))

    assert result["status"] == "FAIL"
    assert "could not load plugin zz_broken.py" in result["detail"]


def test_a_channel_whose_hooks_refuse_the_cli_fails_the_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(release._install_smoke, "_VALIDATE", release._install_smoke._VALIDATE.replace(
        "result.allowed", "False"))

    result = _gate(_package(tmp_path))

    assert result["status"] == "FAIL"
    assert "gaia-orchestrator: `gaia now` refused" in result["detail"]
