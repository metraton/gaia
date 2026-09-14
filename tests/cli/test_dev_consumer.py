"""Consumer-engine regressions: real orchestration, fake npm/pnpm and wiring."""

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))
from cli import dev
from gaia import install_provenance


@pytest.fixture
def consumer(tmp_path, monkeypatch, _isolate_gaia_data_dir):
    """Isolate personal state and intercept every external command."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GAIA_DB", str(tmp_path / "data/gaia.db"))
    workspace = tmp_path / "consumer"
    workspace.mkdir()
    (workspace / "package.json").write_text('{"name":"consumer","dependencies":{"foreign":"1"}}')
    state = {"pm": "npm", "version": 0, "failure": None, "calls": [], "commands": [],
             "section": "dependencies"}
    source = tmp_path / "source"
    (source / "build").mkdir(parents=True)
    (source / "build/gaia.manifest.json").write_text("{}")
    (source / "tests").mkdir()
    (source / "bin").mkdir()
    (source / "bin/gaia").write_text("source executable")
    (source / "package.json").write_text('{"name":"@jaguilar87/gaia"}')
    state["source"] = source
    monkeypatch.setattr(dev, "_PACKAGE_ROOT", source)
    monkeypatch.setattr(dev, "default_pack_dest", lambda ws: tmp_path / "packs")
    monkeypatch.setattr(dev, "record_dev_build", lambda *a: None)
    monkeypatch.setattr(install_provenance, "git_metadata", lambda source: {
        "commit": "a" * 40, "branch": "fixture", "dirty": False,
    })
    monkeypatch.setattr(dev, "_print_convergence_report", lambda *a, **k: {})

    def pack(root, dest_dir):
        state["calls"].append("pack")
        assert root == dev._PACKAGE_ROOT
        state["version"] += 1
        tarball = dest_dir / "gaia.tgz"
        tarball.write_text(str(state["version"]))
        return {"action": "created", "path": str(tarball), "details": "fake pack",
                "tarball": tarball, "version": str(state["version"]), "name": "gaia"}

    def runner(command, **kwargs):
        assert Path(kwargs["cwd"]) == workspace
        package = workspace / "node_modules/@jaguilar87/gaia"
        if command[0] not in ("npm", "pnpm"):
            state["calls"].append("wire")
            assert command[1] == str(package / "bin/gaia")
            assert command[command.index("--workspace") + 1] == str(workspace)
            assert "--no-path" in command and "--strict-wiring" in command
            return subprocess.CompletedProcess(command, int(state["failure"] == "wire"), "", "wire failed")
        assert command[0] == state["pm"]
        assert "--prefix" not in command and "--dir" not in command
        state["commands"].append(command)
        if command[1] in ("ls", "list"):
            state["calls"].append("resolve")
            target = package.resolve()
            if state["failure"] == "resolution-path":
                target = tmp_path / "foreign"
            section = state["section"] if state["pm"] == "pnpm" else "dependencies"
            data = {"path": str(workspace), section: {"@jaguilar87/gaia": {"path": str(target)}}}
            if state["pm"] == "pnpm":
                data = [data]
            return subprocess.CompletedProcess(command, int(state["failure"] == "resolution"), json.dumps(data), "")
        if command[1] == "link":
            assert state["pm"] == "pnpm" and command == ["pnpm", "link", str(source)]
            state["calls"].append("link")
            if state["failure"] == "link":
                return subprocess.CompletedProcess(command, 1, "", "injected link failure")
            if package.is_symlink():
                package.unlink()
            elif package.exists():
                shutil.rmtree(package)
            package.parent.mkdir(parents=True, exist_ok=True)
            if state["failure"] == "copy-instead-of-link":
                shutil.copytree(source, package)
            else:
                package.symlink_to(source)
            return subprocess.CompletedProcess(command, 0, "", "")
        assert command[1] == ("add" if state["pm"] == "pnpm" else "install")
        state["calls"].append("install")
        section_flag = ({"dependencies": "-P", "devDependencies": "-D", "optionalDependencies": "-O"}
                        if state["pm"] == "pnpm" else
                        {"dependencies": "--save-prod", "devDependencies": "--save-dev",
                         "optionalDependencies": "--save-optional"})[state["section"]]
        assert section_flag in command
        source_mode = Path(command[-1]).is_dir()
        if source_mode and state["pm"] == "npm":
            assert "--install-links=false" in command
        manifest = json.loads((workspace / "package.json").read_text())
        spec = "file:" + os.path.relpath(command[-1], workspace)
        manifest.setdefault(state["section"], {})["@jaguilar87/gaia"] = spec
        (workspace / "package.json").write_text(json.dumps(manifest))
        lock = workspace / ("pnpm-lock.yaml" if state["pm"] == "pnpm" else "package-lock.json")
        lock.write_text(json.dumps({"GaiaSpec": spec, "foreign": "unchanged"}))
        if state["failure"] == "install":
            return subprocess.CompletedProcess(command, 1, "", "injected partial install")
        if state["failure"] == "retained-source-in-pack" and not source_mode:
            return subprocess.CompletedProcess(command, 0, "", "")
        if package.is_symlink():
            package.unlink()
        if source_mode and state["pm"] == "npm":
            if package.exists():
                shutil.rmtree(package)
            package.parent.mkdir(parents=True, exist_ok=True)
            if state["failure"] == "copy-instead-of-link":
                shutil.copytree(source, package)
            else:
                package.symlink_to(source)
            target = source
        elif source_mode:
            target = package
            target.mkdir(parents=True, exist_ok=True)
            (target / "bin").mkdir(exist_ok=True)
            (target / "bin/gaia").write_text("hard-linked placeholder")
            (target / "package.json").write_text('{"name":"@jaguilar87/gaia"}')
        elif state["pm"] == "pnpm":
            target = workspace / f"node_modules/.pnpm/gaia-{state['version']}/node_modules/@jaguilar87/gaia"
            target.mkdir(parents=True)
            package.parent.mkdir(parents=True, exist_ok=True)
            if package.is_symlink():
                package.unlink()
            package.symlink_to(target)
        else:
            target = package
            target.mkdir(parents=True, exist_ok=True)
        if not source_mode:
            (target / "bin").mkdir(exist_ok=True)
            (target / "bin/gaia").write_text("fake Gaia entrypoint")
            (target / "package.json").write_text('{"name":"@jaguilar87/gaia"}')
        binary = workspace / "node_modules/.bin/gaia"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text("package-manager bin shim " + ("source" if source_mode else str(state["version"])))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(dev._pack_helpers, "pack_tarball", pack)
    monkeypatch.setattr(dev.subprocess, "run", runner)

    def run():
        return dev.cmd_dev(argparse.Namespace(workspace=str(workspace),
                                             pack_dest=str(tmp_path / "packs"), quiet=True))
    return workspace, state, run


@pytest.mark.parametrize("pm", ["npm", "pnpm"])
@pytest.mark.parametrize("section", ["dependencies", "devDependencies", "optionalDependencies"])
def test_repeat_installs_use_consumer_graph_and_ignore_pycache(consumer, pm, section):
    workspace, state, run = consumer
    state.update(pm=pm, section=section)
    manifest = json.loads((workspace / "package.json").read_text())
    manifest.setdefault(section, {})["@jaguilar87/gaia"] = "1.0.0"
    (workspace / "package.json").write_text(json.dumps(manifest))
    if pm == "pnpm":
        (workspace / "pnpm-lock.yaml").write_text("existing consumer")
    assert run() == 0
    package = workspace / "node_modules/@jaguilar87/gaia"
    cache = package / "__pycache__"
    cache.mkdir()
    (cache / "runtime.pyc").write_bytes(b"runtime-owned bytes")
    previous = json.loads((workspace / "package.json").read_text())[section]["@jaguilar87/gaia"]
    assert run() == 0
    manifest = json.loads((workspace / "package.json").read_text())
    current = manifest[section]["@jaguilar87/gaia"]
    assert current != previous
    assert manifest["dependencies"]["foreign"] == "1"
    assert (workspace / previous[5:]).is_file()
    assert (workspace / current[5:]).is_file()
    lock = workspace / ("pnpm-lock.yaml" if pm == "pnpm" else "package-lock.json")
    assert json.loads(lock.read_text())["GaiaSpec"] == current
    assert (workspace / "node_modules/.bin/gaia").read_text().endswith("2")
    assert state["calls"] == ["pack", "install", "wire", "resolve", "pack", "install", "wire"]
    assert not (workspace / ".gaia-dev").exists()


@pytest.mark.parametrize("failure", ["resolution", "resolution-path"])
def test_disagreement_refuses_before_pack(consumer, failure):
    workspace, state, run = consumer
    assert run() == 0
    before = (workspace / "package.json").read_bytes()
    state["failure"] = failure
    assert run() == 1
    assert state["calls"][-1] == "resolve"
    assert (workspace / "package.json").read_bytes() == before


@pytest.mark.parametrize("pm", ["npm", "pnpm"])
@pytest.mark.parametrize("failure", ["install", "wire"])
def test_failure_exposes_partial_consumer_effects_not_fake_rollback(consumer, pm, failure):
    workspace, state, run = consumer
    state.update(pm=pm, failure=failure)
    if pm == "pnpm":
        (workspace / "pnpm-lock.yaml").write_text("existing graph")
    assert run() == 1
    manifest = json.loads((workspace / "package.json").read_text())
    assert manifest["dependencies"]["@jaguilar87/gaia"].startswith("file:")
    assert manifest["dependencies"]["foreign"] == "1"
    assert ("wire" in state["calls"]) == (failure == "wire")
    assert not (workspace / ".gaia-dev").exists()


def test_name_alone_is_not_package_manager_ownership(consumer):
    workspace, state, run = consumer
    package = workspace / "node_modules/@jaguilar87/gaia"
    (package / "bin").mkdir(parents=True)
    (package / "package.json").write_text('{"name":"@jaguilar87/gaia"}')
    (package / "bin/gaia").write_text("foreign sentinel")
    assert run() == 1
    assert state["calls"] == []
    assert (package / "bin/gaia").read_text() == "foreign sentinel"


@pytest.mark.parametrize("pm", ["npm", "pnpm"])
def test_legacy_source_link_is_replaced_by_a_tarball_install(consumer, pm):
    """A `node_modules/@jaguilar87/gaia` symlink left over from Gaia's
    removed source-linking dev mode is tolerated by the ownership check
    (`existing_package_error`'s `legacy_source_root` allowance) so pack can
    proceed, then the package manager's own install replaces it with a
    normal tarball artifact -- pack never leaves that legacy symlink in place."""
    workspace, state, run = consumer
    state["pm"] = pm
    if pm == "pnpm":
        (workspace / "pnpm-lock.yaml").write_text("consumer")
    package = workspace / "node_modules/@jaguilar87/gaia"
    package.parent.mkdir(parents=True)
    package.symlink_to(state["source"])
    assert run() == 0
    # pnpm still symlinks (into its own .pnpm store, not the source
    # checkout); npm materializes a plain directory. Either way, the
    # package no longer resolves to the legacy source symlink target.
    assert package.resolve() != state["source"]


@pytest.mark.parametrize("pm", ["npm", "pnpm"])
def test_pack_never_wires_a_retained_source_link(consumer, pm):
    """`install_tarball` fails loud if the package manager still leaves the
    package pointed at a source checkout after a reported-successful
    install; wiring must never run on top of that."""
    workspace, state, run = consumer
    state["pm"] = pm
    if pm == "pnpm":
        (workspace / "pnpm-lock.yaml").write_text("consumer")
    package = workspace / "node_modules/@jaguilar87/gaia"
    package.parent.mkdir(parents=True)
    package.symlink_to(state["source"])
    state["failure"] = "retained-source-in-pack"
    assert run() == 1
    assert "wire" not in state["calls"]
    assert (workspace / "node_modules/@jaguilar87/gaia").resolve() == state["source"]


@pytest.mark.parametrize("pm", ["npm", "pnpm"])
def test_source_ownership_probe_requires_spec_source_and_resolver(consumer, tmp_path, pm):
    workspace, state, _ = consumer
    state["pm"] = pm
    if pm == "pnpm":
        (workspace / "pnpm-lock.yaml").write_text("consumer graph")
    source = tmp_path / "probe-source"
    (source / "build").mkdir(parents=True)
    (source / "build/gaia.manifest.json").write_text("{}")
    (source / "tests").mkdir()
    (source / "bin").mkdir()
    (source / "bin/gaia").write_text("source sentinel")
    (source / "package.json").write_text('{"name":"@jaguilar87/gaia"}')
    package = workspace / "node_modules/@jaguilar87/gaia"
    package.parent.mkdir(parents=True)
    package.symlink_to(source)
    manifest = workspace / "package.json"
    manifest.write_text(json.dumps({"dependencies": {"@jaguilar87/gaia": "file:" + str(source)}}))
    assert dev.existing_package_error(workspace, allow_source_links=True) is None
    state["failure"] = "resolution-path"
    assert dev.existing_package_error(workspace, allow_source_links=True) is not None
    state["failure"] = None
    manifest.write_text(json.dumps({"dependencies": {"@jaguilar87/gaia": "file:../foreign"}}))
    assert dev.existing_package_error(workspace, allow_source_links=True) is not None
    assert (source / "bin/gaia").read_text() == "source sentinel"
    assert state["calls"] == ["resolve", "resolve"]


@pytest.mark.parametrize("failure", ["install", "wire"])
def test_recovery_evidence_precedes_mutation_and_preserves_original_spec(consumer, failure):
    workspace, state, run = consumer
    assert run() == 0
    before = (workspace / "package.json").read_bytes()
    old_spec = json.loads(before)["dependencies"]["@jaguilar87/gaia"]
    state["failure"] = failure
    assert run() == 1
    records = [json.loads(path.read_text()) for path in (workspace.parent / "packs").glob("attempt-*/recovery.json")]
    failed = next(record for record in records if record["status"] == "recovery-required")
    assert failed["stage"] == failure
    assert base64.b64decode(failed["before"]["files"]["package.json"]["bytes_base64"]) == before
    assert failed["before"]["package"]["present"]
    assert (workspace / old_spec[5:]).is_file()
    assert "not performed" in failed["rollback"]


def test_refuses_install_when_pre_mutation_snapshot_cannot_be_written(consumer, monkeypatch):
    _, state, run = consumer
    def denied(*args, **kwargs):
        raise OSError("injected recovery path failure")
    monkeypatch.setattr(dev, "write_recovery_evidence", denied)
    assert run() == 1
    assert state["calls"] == ["pack"]


def test_foreign_change_is_observed_not_restored(consumer, monkeypatch):
    workspace, _, run = consumer
    def foreign_writer(*args, **kwargs):
        (workspace / "package.json").write_text('{"foreign":"concurrent edit"}')
        return {"action": "error", "path": "x", "details": "injected wiring failure"}
    monkeypatch.setattr(dev, "wire_workspace_via_installed_gaia", foreign_writer)
    assert run() == 1
    assert (workspace / "package.json").read_text() == '{"foreign":"concurrent edit"}'
    record = json.loads(next((workspace.parent / "packs").glob("attempt-*/recovery.json")).read_text())
    assert record["status"] == "recovery-required"
    assert base64.b64decode(record["observed_after"]["files"]["package.json"]["bytes_base64"]) == b'{"foreign":"concurrent edit"}'


def test_success_records_common_provenance_after_wiring(consumer):
    workspace, state, run = consumer
    assert run() == 0
    payload = json.loads(install_provenance.provenance_path(workspace).read_text())
    assert payload["source_path"] == str(state["source"].resolve())
    assert payload["destination"] == str((workspace / "node_modules/@jaguilar87/gaia").resolve())
    # "source-snapshot" was link mode's kind (`capture_source` called with no
    # tarball); `gaia dev` always packs now, so this is always "tarball".
    assert payload["tarball_hash_kind"] == "tarball"
    spec = json.loads((workspace / "package.json").read_text())["dependencies"]["@jaguilar87/gaia"]
    assert install_provenance.inspect_install(workspace, spec)["diagnostics"] == []


def test_failed_wiring_never_publishes_success_provenance(consumer):
    workspace, state, run = consumer
    state["failure"] = "wire"
    assert run() == 1
    assert not install_provenance.provenance_path(workspace).exists()


def test_record_failure_exposes_partial_install_without_success(consumer, monkeypatch, capsys):
    workspace, _, run = consumer

    def denied(*args):
        """Simulate unavailable state storage after consumer wiring."""
        raise OSError("record storage denied")

    monkeypatch.setattr(install_provenance, "record_install", denied)
    assert run() == 1
    assert "no rollback performed" in capsys.readouterr().err
    assert not install_provenance.provenance_path(workspace).exists()
    records = list((workspace.parent / "packs").glob("*/recovery.json"))
    assert len(records) == 1
    recovery = json.loads(records[0].read_text())
    assert recovery["stage"] == "provenance"
    assert recovery["status"] == "recovery-required"


def test_capture_failure_prevents_consumer_install(consumer, monkeypatch):
    workspace, state, run = consumer

    def denied(*args, **kwargs):
        """Simulate unreadable source before consumer mutation."""
        raise OSError("source unreadable")

    monkeypatch.setattr(install_provenance, "capture_source", denied)
    assert run() == 1
    assert "install" not in state["calls"]
    assert not install_provenance.provenance_path(workspace).exists()
