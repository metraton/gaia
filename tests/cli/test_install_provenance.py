"""Common provenance uses real Git/files and the doctor serializer, without installing hosts."""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))
from cli import doctor
from gaia import install_provenance as provenance


def git(source, *args):
    """Run Git only against the disposable source fixture."""
    return subprocess.run(["git", "-C", str(source), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def commit(source, message):
    """Create a fixture commit with process-local identity, leaving repository config untouched."""
    git(source, "add", "main.py")
    git(source, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-m", message)


@pytest.fixture
def installation(tmp_path, _isolate_gaia_data_dir):
    """Provide a real source repo and independent consumer tree, not a real Gaia install."""
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init")
    (source / "main.py").write_text("print('source')\n")
    commit(source, "initial")
    workspace = tmp_path / "consumer"
    destination = workspace / "node_modules/@jaguilar87/gaia"
    destination.mkdir(parents=True)
    (destination / "package.json").write_text('{"name":"@jaguilar87/gaia","version":"5.4.0-rc.1"}')
    (destination / "main.py").write_text("print('installed')\n")
    tarball = tmp_path / "artifact.tgz"
    tarball.write_bytes(b"fixture artifact, not an npm pack")
    (workspace / "package.json").write_text(json.dumps({
        "dependencies": {"@jaguilar87/gaia": f"file:{tarball}"},
    }))
    captured = provenance.capture_source(source, tarball=tarball)
    marker = provenance.record_install(workspace, captured)
    return source, workspace, destination, tarball, marker


def test_pack_record_and_doctor_match(installation):
    source, workspace, destination, tarball, marker = installation
    data = json.loads(marker.read_text())
    assert data["source_path"] == str(source.resolve())
    assert data["commit"] == git(source, "rev-parse", "HEAD")
    assert data["branch"] == git(source, "branch", "--show-current")
    assert data["dirty"] is False
    assert data["tarball_hash"] == hashlib.sha256(tarball.read_bytes()).hexdigest()
    assert data["tarball_hash_kind"] == "tarball"
    assert data["destination"] == str(destination.resolve())
    result = doctor.check_install_provenance(workspace)
    assert result["severity"] == "pass"
    assert result["provenance"]["diagnostics"] == []


@pytest.mark.parametrize("change,diagnostic", [
    ("commit", "source commit diverged"),
    ("branch", "source branch diverged"),
    ("dirty", "source dirty diverged"),
    ("source", "source content/hash diverged"),
    ("tarball", "tarball hash diverged"),
    ("destination", "installed destination diverged"),
    ("installed", "installed content/hash diverged"),
    ("spec", "installed dependency spec diverged"),
    ("path", "source path diverged"),
])
def test_doctor_detects_drift(installation, change, diagnostic):
    source, workspace, destination, tarball, _ = installation
    if change in ("commit", "dirty", "source"):
        (source / "main.py").write_text("changed\n")
        if change == "commit":
            commit(source, "changed")
    elif change == "branch":
        git(source, "switch", "-c", "another")
    elif change == "tarball":
        tarball.write_bytes(b"different bytes")
    elif change == "destination":
        other = destination.with_name("other")
        destination.rename(other)
        destination.symlink_to(other, target_is_directory=True)
    elif change == "installed":
        (destination / "main.py").write_text("changed install\n")
    elif change == "spec":
        (workspace / "package.json").write_text('{}')
    elif change == "path":
        other = source.with_name("moved")
        source.rename(other)
        source.symlink_to(other, target_is_directory=True)
    result = doctor.check_install_provenance(workspace)
    assert result["severity"] == "error"
    assert diagnostic in result["provenance"]["diagnostics"]


def test_link_hash_is_explicit_and_observes_dirty_to_dirty_changes(installation):
    source, workspace, destination, _, _ = installation
    moved = destination.with_name("prior-pack")
    destination.rename(moved)
    destination.symlink_to(source, target_is_directory=True)
    (workspace / "package.json").write_text(json.dumps({
        "devDependencies": {"@jaguilar87/gaia": f"link:{source}"},
    }))
    (source / "main.py").write_text("dirty once\n")
    provenance.record_install(workspace, provenance.capture_source(source))
    before = doctor.check_install_provenance(workspace)
    assert before["severity"] == "pass"
    assert before["provenance"]["dirty"] is True
    assert before["provenance"]["tarball_hash_kind"] == "source-snapshot"
    assert before["provenance"]["tarball_path"] is None
    (source / "main.py").write_text("dirty twice\n")
    after = doctor.check_install_provenance(workspace)
    assert after["severity"] == "error"
    assert "source content/hash diverged" in after["provenance"]["diagnostics"]
    assert "source-snapshot hash diverged" in after["provenance"]["diagnostics"]


@pytest.mark.parametrize("contents", ["not json", "[]", '{"schema":999}', '{"schema":1}'])
def test_invalid_records_fail_loud(installation, contents):
    _, workspace, _, _, marker = installation
    marker.write_text(contents)
    result = doctor.check_install_provenance(workspace)
    assert result["severity"] == "error"
    assert result["provenance"]["diagnostics"][0].startswith("invalid provenance")


def test_unknown_git_state_is_not_reported_clean(tmp_path):
    result = provenance.capture_source(tmp_path)
    assert result["commit"] is None
    assert result["branch"] is None
    assert result["dirty"] is None


def test_cache_and_symlink_snapshot_policy(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("external")
    (root / "link").symlink_to(outside)
    before = provenance.source_snapshot_hash(root)
    outside.write_text("changed externally")
    cache = root / "__pycache__"
    cache.mkdir()
    (cache / "noise.pyc").write_bytes(b"cache")
    assert provenance.source_snapshot_hash(root) == before
    (root / "link").unlink()
    (root / "link").symlink_to(tmp_path / "different")
    assert provenance.source_snapshot_hash(root) != before


def test_record_identity_is_canonical_consumer_not_basename(tmp_path):
    first = tmp_path / "one/consumer"
    second = tmp_path / "two/consumer"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(first, target_is_directory=True)
    assert provenance.provenance_path(first) == provenance.provenance_path(alias)
    assert provenance.provenance_path(first) != provenance.provenance_path(second)


def test_doctor_json_before_and_after_drift(installation, monkeypatch, capsys):
    source, workspace, _, tarball, _ = installation
    monkeypatch.setattr(doctor, "_derive_workspace", lambda **kwargs: workspace)
    monkeypatch.setattr(doctor, "_CHECKS", [(57, "Install provenance", doctor.check_install_provenance)])
    args = argparse.Namespace(workspace=str(workspace), json=True, fix=False)
    assert doctor.cmd_doctor(args) == 0
    before = json.loads(capsys.readouterr().out)
    assert before["healthy"] is True
    tarball.write_bytes(b"drift")
    assert doctor.cmd_doctor(args) == 2
    after = json.loads(capsys.readouterr().out)
    assert after["healthy"] is False
    fields = after["checks"][0]["provenance"]
    assert fields["source_path"] == str(source)
    assert fields["diagnostics"] == ["tarball hash diverged"]
    print(json.dumps({"before": before, "after": after}, sort_keys=True))


def test_registry_write_does_not_follow_foreign_marker(installation):
    source, workspace, _, tarball, marker = installation
    foreign = marker.with_name("foreign.json")
    foreign.write_text("sentinel")
    marker.unlink()
    marker.symlink_to(foreign)
    with pytest.raises(ValueError, match="redirected"):
        provenance.record_install(workspace, provenance.capture_source(source, tarball=tarball))
    assert foreign.read_text() == "sentinel"
    assert doctor.check_install_provenance(workspace)["severity"] == "error"


@pytest.mark.parametrize("missing", ["source", "tarball", "destination"])
def test_missing_paths_are_diagnostics_not_healthy(installation, missing):
    source, workspace, destination, tarball, _ = installation
    target = {"source": source, "tarball": tarball, "destination": destination}[missing]
    target.rename(target.with_name("moved-away"))
    result = doctor.check_install_provenance(workspace)
    assert result["severity"] == "error"
    assert any("unavailable" in item for item in result["provenance"]["diagnostics"])


def test_record_replace_failure_preserves_previous_observation(installation, monkeypatch):
    source, workspace, _, tarball, marker = installation
    before = marker.read_bytes()

    def denied(*args):
        """Fail publication after the unique temporary record has been written."""
        raise OSError("replace denied")

    monkeypatch.setattr(provenance.os, "replace", denied)
    with pytest.raises(OSError, match="replace denied"):
        provenance.record_install(workspace, provenance.capture_source(source, tarball=tarball))
    assert marker.read_bytes() == before
    assert list(marker.parent.glob("*.pending")) == []


def test_foreign_record_is_not_overwritten(installation):
    source, workspace, _, tarball, marker = installation
    marker.write_text('{"schema":1,"workspace":"/another/consumer"}')
    before = marker.read_bytes()
    with pytest.raises(ValueError, match="unowned"):
        provenance.record_install(workspace, provenance.capture_source(source, tarball=tarball))
    assert marker.read_bytes() == before
