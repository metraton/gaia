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
    monkeypatch.setattr(dev, "_print_convergence_report", lambda *a, **k: {})
    monkeypatch.setattr(dev.install_mod, "reconcile_global_via_npm_link",
                        lambda *a, **k: pytest.fail("global npm link forbidden"))

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
    def source_wire(ns):
        assert ns.workspace == str(workspace) and ns.no_path and ns.strict_wiring
        state["calls"].append("source-wire")
        return int(state["failure"] == "wire")
    monkeypatch.setattr(dev.install_mod, "cmd_install", source_wire)

    def run(mode="pack"):
        return dev.cmd_dev(argparse.Namespace(workspace=str(workspace),
                                             pack_dest=str(tmp_path / "packs"), quiet=True, mode=mode))
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
@pytest.mark.parametrize("section", ["dependencies", "devDependencies", "optionalDependencies"])
def test_native_pack_link_repeat_pack_transitions(consumer, pm, section):
    workspace, state, run = consumer
    state.update(pm=pm, section=section)
    if pm == "pnpm":
        (workspace / "pnpm-lock.yaml").write_text("consumer")
    # Establish the section before the first manager call, as real consumers do.
    manifest = json.loads((workspace / "package.json").read_text())
    manifest.setdefault(section, {})["@jaguilar87/gaia"] = "1.0.0"
    (workspace / "package.json").write_text(json.dumps(manifest))
    for mode in ("pack", "link", "link", "pack"):
        assert run(mode) == 0
        package = workspace / "node_modules/@jaguilar87/gaia"
        metadata = json.loads((workspace / "package.json").read_text())
        assert metadata["dependencies"]["foreign"] == "1"
        spec = metadata[section]["@jaguilar87/gaia"]
        lock = workspace / ("pnpm-lock.yaml" if pm == "pnpm" else "package-lock.json")
        assert json.loads(lock.read_text())["GaiaSpec"] == spec
        if mode == "link":
            assert package.is_symlink() and package.resolve() == state["source"]
            assert spec.startswith("file:")
            assert (workspace / "node_modules/.bin/gaia").read_text().endswith("source")
            assert state["calls"][-1] == "source-wire"
        else:
            assert package.resolve() != state["source"]
            assert state["calls"][-1] == "wire"
    assert (state["source"] / "bin/gaia").read_text() == "source executable"
    assert not (workspace / ".gaia-dev").exists()


@pytest.mark.parametrize("pm", ["npm", "pnpm"])
def test_legacy_same_source_is_saved_by_manager(consumer, pm):
    workspace, state, run = consumer
    state["pm"] = pm
    if pm == "pnpm":
        (workspace / "pnpm-lock.yaml").write_text("consumer")
    package = workspace / "node_modules/@jaguilar87/gaia"
    package.parent.mkdir(parents=True)
    package.symlink_to(state["source"])
    assert run("link") == 0
    assert run("pack") == 0


@pytest.mark.parametrize("pm,failure", [
    ("npm", "install"), ("npm", "copy-instead-of-link"),
    ("pnpm", "install"), ("pnpm", "link"), ("pnpm", "copy-instead-of-link"),
])
def test_bad_source_switch_never_wires(consumer, failure, pm):
    workspace, state, run = consumer
    state["pm"] = pm
    if pm == "pnpm":
        (workspace / "pnpm-lock.yaml").write_text("consumer")
    assert run() == 0
    state["failure"] = failure
    assert run("link") == 1
    assert "source-wire" not in state["calls"]
    records = [json.loads(p.read_text()) for p in (workspace.parent / "packs").glob("link-attempt-*/recovery.json")]
    assert records[-1]["status"] == "recovery-required"


@pytest.mark.parametrize("section", ["dependencies", "devDependencies", "optionalDependencies"])
def test_pnpm_link_persists_consumer_state_without_global_pm_state(consumer, section):
    workspace, state, run = consumer
    state.update(pm="pnpm", section=section)
    (workspace / "pnpm-lock.yaml").write_text("consumer")
    manifest = json.loads((workspace / "package.json").read_text())
    manifest.setdefault(section, {})["@jaguilar87/gaia"] = "1.0.0"
    (workspace / "package.json").write_text(json.dumps(manifest))
    assert run("link") == 0
    assert state["commands"][:2] == [
        ["pnpm", "add", {"dependencies": "-P", "devDependencies": "-D",
                           "optionalDependencies": "-O"}[section], str(state["source"])],
        ["pnpm", "link", str(state["source"])],
    ]
    assert all("--global" not in command and "-g" not in command for command in state["commands"])


@pytest.mark.parametrize("section", ["dependencies", "devDependencies", "optionalDependencies"])
def test_npm_link_uses_external_folder_symlink_semantics(consumer, section):
    workspace, state, run = consumer
    state["section"] = section
    manifest = json.loads((workspace / "package.json").read_text())
    manifest.setdefault(section, {})["@jaguilar87/gaia"] = "1.0.0"
    (workspace / "package.json").write_text(json.dumps(manifest))
    assert run("link") == 0
    assert state["commands"][0] == [
        "npm", "install", "--no-audit", "--no-fund",
        {"dependencies": "--save-prod", "devDependencies": "--save-dev",
         "optionalDependencies": "--save-optional"}[section],
        "--install-links=false", str(state["source"]),
    ]
    assert all("--global" not in command and "-g" not in command for command in state["commands"])


def test_source_within_consumer_refused(consumer, monkeypatch):
    workspace, state, run = consumer
    monkeypatch.setattr(dev, "_PACKAGE_ROOT", workspace)
    monkeypatch.setattr(dev, "_is_source_checkout", lambda root: True)
    assert run("link") == 1
    assert state["calls"] == []


@pytest.mark.parametrize("pm", ["npm", "pnpm"])
def test_pack_never_wires_retained_source_link(consumer, pm):
    workspace, state, run = consumer
    state["pm"] = pm
    if pm == "pnpm":
        (workspace / "pnpm-lock.yaml").write_text("consumer")
    assert run("link") == 0
    state["failure"] = "retained-source-in-pack"
    assert run("pack") == 1
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
