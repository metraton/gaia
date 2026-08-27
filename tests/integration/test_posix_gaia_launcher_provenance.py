"""POSIX installer-to-PATH coverage for Gaia package provenance."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / "bin"
HOOKS_DIR = REPO_ROOT / "hooks"
for import_root in (BIN_DIR, HOOKS_DIR):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from cli.install import _install_path_launcher, _render_launcher  # noqa: E402
from modules.security.gaia_cli_only_guard import (  # noqa: E402
    is_trusted_gaia_binary,
)
from modules.tools.bash_validator import BashValidator  # noqa: E402


def _install_real_package_through_public_flow(tmp_path: Path) -> dict:
    """Pack, install, and configure Gaia through its public POSIX flow."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    packed = subprocess.run(
        [
            "npm",
            "pack",
            "--json",
            "--pack-destination",
            str(artifacts),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert packed.returncode == 0, packed.stderr
    pack_payload = json.loads(packed.stdout)
    tarball = artifacts / pack_payload[0]["filename"]
    assert tarball.is_file()

    prefix = tmp_path / "prefix"
    installed = subprocess.run(
        [
            "npm",
            "install",
            "--prefix",
            str(prefix),
            str(tarball),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert installed.returncode == 0, installed.stderr

    package_root = prefix / "node_modules" / "@jaguilar87" / "gaia"
    manifest = json.loads((package_root / "package.json").read_text())
    declared_target = (package_root / manifest["bin"]["gaia"]).resolve()
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    db_path = tmp_path / "data" / "gaia.db"
    env = {
        **os.environ,
        "HOME": str(home),
        "GAIA_DB": str(db_path),
        "INIT_CWD": str(workspace),
    }
    public_install = subprocess.run(
        [
            sys.executable,
            str(declared_target),
            "install",
            "--workspace",
            str(workspace),
            "--host",
            "opencode",
            "--db-path",
            str(db_path),
            "--quiet",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert public_install.returncode == 0, public_install.stderr
    return {
        "package_root": package_root,
        "manifest": manifest,
        "declared_target": declared_target,
        "launcher": home / ".local" / "bin" / "gaia",
        "env": env,
        "pack_returncode": packed.returncode,
        "npm_install_returncode": installed.returncode,
        "public_install_returncode": public_install.returncode,
    }


def _validate_bare_gaia(path: str):
    previous_path = os.environ.get("PATH", "")
    os.environ["PATH"] = path
    try:
        return BashValidator().validate(
            "gaia contract list --json",
            hook_payload={},
        )
    finally:
        os.environ["PATH"] = previous_path


def test_production_launcher_preserves_installed_package_provenance(tmp_path):
    installed = _install_real_package_through_public_flow(tmp_path)
    package_root = installed["package_root"]
    manifest = installed["manifest"]
    declared_target = installed["declared_target"]
    launcher = installed["launcher"]
    isolated_path = f"{launcher.parent}{os.pathsep}{os.environ['PATH']}"

    assert launcher.is_symlink()
    assert manifest["name"] == "@jaguilar87/gaia"
    assert manifest["bin"]["gaia"] == "bin/gaia"
    assert launcher.resolve() == declared_target.resolve()
    previous_path = os.environ.get("PATH", "")
    os.environ["PATH"] = isolated_path
    try:
        resolved_launcher = shutil.which("gaia")
        assert resolved_launcher == str(launcher)
        assert is_trusted_gaia_binary(resolved_launcher)
        verdict = BashValidator().validate(
            "gaia contract list --json",
            hook_payload={},
        )
    finally:
        os.environ["PATH"] = previous_path
    assert verdict.allowed is True, verdict.reason

    result = subprocess.run(
        ["gaia", "contract", "list", "--json"],
        env={
            **installed["env"],
            "PATH": isolated_path,
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "count" in json.loads(result.stdout)
    print(
        "PROVENANCE_EVIDENCE="
        + json.dumps(
            {
                "resolved_launcher": resolved_launcher,
                "package_identity": manifest["name"],
                "declared_bin_gaia": str(declared_target),
                "resolution_chain": [str(launcher), str(launcher.resolve())],
                "trusted": True,
                "validator_allowed": verdict.allowed,
                "command_returncode": result.returncode,
                "command_result": json.loads(result.stdout),
                "pack_returncode": installed["pack_returncode"],
                "npm_install_returncode": installed["npm_install_returncode"],
                "public_install_returncode": installed[
                    "public_install_returncode"
                ],
            },
            sort_keys=True,
        )
    )


def test_forwarding_wrapper_copy_and_earlier_path_winner_are_untrusted(tmp_path):
    installed = _install_real_package_through_public_flow(tmp_path)
    declared_target = installed["declared_target"]
    trusted = installed["launcher"]
    trusted_dir = trusted.parent

    wrapper_dir = tmp_path / "wrapper-bin"
    wrapper_dir.mkdir()
    wrapper = wrapper_dir / "gaia"
    wrapper.write_text(f'#!/bin/sh\nexec "{declared_target}" "$@"\n')
    wrapper.chmod(0o755)

    copy_dir = tmp_path / "copy-bin"
    copy_dir.mkdir()
    copied = copy_dir / "gaia"
    shutil.copy2(declared_target, copied)

    earlier_dir = tmp_path / "earlier-bin"
    earlier_dir.mkdir()
    earlier = earlier_dir / "gaia"
    earlier.write_text("#!/bin/sh\nexit 0\n")
    earlier.chmod(0o755)

    for path in (wrapper_dir, copy_dir):
        winner = shutil.which("gaia", path=str(path))
        assert winner is not None
        assert not is_trusted_gaia_binary(winner)
        verdict = _validate_bare_gaia(f"{path}{os.pathsep}{trusted_dir}")
        assert verdict.allowed is False
        assert "not the trusted gaia CLI" in verdict.reason

    winner = shutil.which(
        "gaia", path=f"{earlier_dir}{os.pathsep}{trusted_dir}"
    )
    assert winner == str(earlier)
    assert not is_trusted_gaia_binary(winner)
    verdict = _validate_bare_gaia(f"{earlier_dir}{os.pathsep}{trusted_dir}")
    assert verdict.allowed is False
    print(
        "NEGATIVE_EVIDENCE="
        + json.dumps(
            {
                "forwarding_wrapper_trusted": is_trusted_gaia_binary(str(wrapper)),
                "byte_copy_trusted": is_trusted_gaia_binary(str(copied)),
                "earlier_path_winner": winner,
                "earlier_path_winner_trusted": is_trusted_gaia_binary(winner),
            },
            sort_keys=True,
        )
    )


def test_launcher_is_idempotent_and_migrates_or_overwrites_compatibly(tmp_path):
    installed = _install_real_package_through_public_flow(tmp_path)
    target = installed["declared_target"]
    launcher = tmp_path / "compat-bin" / "gaia"
    workspace = tmp_path / "compat-workspace"
    workspace.mkdir()

    first = _install_path_launcher(
        link_path=launcher, workspace=workspace, gaia_bin=target
    )
    second = _install_path_launcher(
        link_path=launcher, workspace=workspace, gaia_bin=target
    )
    assert (first["action"], second["action"]) == ("created", "noop")

    launcher.unlink()
    launcher.write_text(_render_launcher(workspace.resolve()))
    migrated = _install_path_launcher(
        link_path=launcher, workspace=workspace, gaia_bin=target
    )
    assert migrated["action"] == "migrated"
    assert launcher.resolve() == target.resolve()

    launcher.unlink()
    launcher.write_text("user-owned wrapper")
    skipped = _install_path_launcher(
        link_path=launcher, workspace=workspace, gaia_bin=target
    )
    assert skipped["action"] == "skipped"
    replaced = _install_path_launcher(
        link_path=launcher,
        workspace=workspace,
        gaia_bin=target,
        overwrite=True,
    )
    assert replaced["action"] == "replaced"
    assert launcher.resolve() == target.resolve()
    print(
        "COMPATIBILITY_EVIDENCE="
        + json.dumps(
            {
                "first": first["action"],
                "second": second["action"],
                "legacy_wrapper": migrated["action"],
                "user_file_without_overwrite": skipped["action"],
                "user_file_with_overwrite": replaced["action"],
            },
            sort_keys=True,
        )
    )
