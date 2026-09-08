"""Host metadata remains available to registry consumers in shipped layouts."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
METADATA = ROOT / "opencode" / "consent-metadata.json"


def test_registered_metadata_matches_plugin_constants():
    """The audit enumeration includes every real plugin permission event."""
    from adapters.registry import get_adapter, registered_host_mechanism_names
    from adapters.opencode import OpenCodeAdapter

    script = (
        "import { PREFERRED_PERMISSION_EVENT, COMPATIBILITY_PERMISSION_EVENTS } "
        f"from {json.dumps(str(ROOT / 'opencode' / 'plugin.ts'))};"
        "console.log(JSON.stringify([PREFERRED_PERMISSION_EVENT,"
        " ...COMPATIBILITY_PERMISSION_EVENTS]));"
    )
    result = subprocess.run(
        ["bun", "-e", script], capture_output=True, text=True, check=True, timeout=30,
    )
    names = json.loads(METADATA.read_text(encoding="utf-8"))["mechanism_names"]
    assert names == json.loads(result.stdout)
    assert registered_host_mechanism_names() == (
        "askuserquestion", "elicitationresult", *names,
    )
    assert isinstance(get_adapter("opencode"), OpenCodeAdapter)
    assert get_adapter("opencode") is get_adapter("opencode")


def test_metadata_is_in_build_and_npm_distribution():
    """Both distribution inventories include the registry dependency."""
    spec = importlib.util.spec_from_file_location("build_plugin", ROOT / "scripts" / "build-plugin.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    manifest = json.loads((ROOT / "build" / "gaia.manifest.json").read_text(encoding="utf-8"))
    assert METADATA in builder.resolve_file_list(manifest)
    result = subprocess.run(
        ["npm", "pack", "--dry-run", "--ignore-scripts", "--json"],
        cwd=ROOT, capture_output=True, text=True, check=True, timeout=60,
    )
    paths = {entry["path"] for entry in json.loads(result.stdout)[0]["files"]}
    assert "opencode/consent-metadata.json" in paths
    assert "hooks/adapters/registry.py" in paths


def test_doctor_checks_every_managed_install_entry(tmp_path, monkeypatch):
    """Doctor and installer inventories cannot silently disagree."""
    monkeypatch.syspath_prepend(str(ROOT / "bin"))
    from cli import _install_helpers as installer, doctor

    observed = []

    def present(path):
        """Record the diagnostic paths without writing an installed tree."""
        observed.append(path.name)
        return True

    with monkeypatch.context() as paths:
        paths.setattr(Path, "exists", present)
        paths.setattr(Path, "resolve", lambda path, **kwargs: path)
        result = doctor.check_symlinks(tmp_path)
    expected = installer._SYMLINK_NAMES + installer._SYMLINK_FILES
    assert observed == expected
    assert result["severity"] == "pass"
    assert result["detail"] == f"{len(expected)}/{len(expected)} valid"


def test_copy_install_inventory_supplies_registry_metadata(tmp_path, monkeypatch):
    """A copied hook tree loads metadata without access to the source checkout."""
    monkeypatch.syspath_prepend(str(ROOT / "bin"))
    from cli import _install_helpers as installer

    assert "opencode" in installer._SYMLINK_NAMES
    installed = tmp_path / "copied-runtime"
    installed.mkdir()
    for name in ("hooks", "opencode", "gaia"):
        installer._materialize_copy(ROOT / name, installed / name)
    script = (
        "import sys; "
        f"sys.path[:0] = {list(map(str, (installed / 'hooks', installed)))!r}; "
        "from adapters.registry import registered_host_mechanism_names; "
        "import json; print(json.dumps(registered_host_mechanism_names()))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script], cwd=installed,
        capture_output=True, text=True, check=True, timeout=30,
    )
    expected = json.loads(METADATA.read_text(encoding="utf-8"))["mechanism_names"]
    assert json.loads(result.stdout) == ["askuserquestion", "elicitationresult", *expected]
