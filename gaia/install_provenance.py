"""Record local dev-install observations and diagnose drift without rewriting consumers."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from gaia.paths import state_dir

_IGNORED = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".project-worktrees"}


def provenance_path(workspace: Path) -> Path:
    """Locate the machine-local record by canonical consumer path, not its basename."""
    key = hashlib.sha256(os.fsencode(workspace.resolve())).hexdigest()
    return state_dir() / "dev-installs" / f"{key}.json"


def file_hash(path: Path) -> str:
    """Hash file bytes without loading the whole artifact into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_snapshot_hash(source: Path) -> str:
    """Hash tree content and symlink text, excluding derived caches without following links."""
    if not source.is_dir():
        raise ValueError(f"snapshot directory missing: {source}")
    entries = []

    def unreadable(error: OSError) -> None:
        """Refuse a partial snapshot when directory enumeration fails."""
        raise error

    for root, dirs, files in os.walk(source, followlinks=False, onerror=unreadable):
        dirs[:] = [name for name in dirs if name not in _IGNORED]
        links = [name for name in dirs if (Path(root) / name).is_symlink()]
        dirs[:] = [name for name in dirs if name not in links]
        for name in files + links:
            if name in _IGNORED:
                continue
            path = Path(root) / name
            if not path.is_symlink() and not path.is_file():
                raise ValueError(f"unsupported snapshot entry: {path}")
            relative = path.relative_to(source).as_posix()
            value = ("link", os.readlink(path)) if path.is_symlink() else ("file", file_hash(path))
            entries.append((relative, *value))
    return hashlib.sha256(json.dumps(sorted(entries), ensure_ascii=True).encode()).hexdigest()


def _git(source: Path, *args: str) -> str | None:
    """Read Git metadata, distinguishing unavailable output from a clean status."""
    try:
        result = subprocess.run(["git", "--no-optional-locks", "-C", str(source), *args],
                                capture_output=True, text=True, check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def git_metadata(source: Path) -> dict[str, Any]:
    """Capture Git identity; unavailable metadata remains unknown rather than clean."""
    status = _git(source, "status", "--porcelain")
    branch = _git(source, "branch", "--show-current")
    return {"commit": _git(source, "rev-parse", "--verify", "HEAD^{commit}"),
            "branch": "(detached HEAD)" if branch == "" else branch,
            "dirty": None if status is None else bool(status)}


def capture_source(source: Path, *, tarball: Path | None = None) -> dict[str, Any]:
    """Capture source and artifact observations before consumer installation begins."""
    source = source.resolve()
    snapshot = source_snapshot_hash(source)
    artifact = tarball.resolve() if tarball is not None else None
    return {"schema": 1, "source_path": str(source), **git_metadata(source),
            "source_hash": snapshot, "tarball_hash_kind": "tarball" if artifact else "source-snapshot",
            "tarball_hash": file_hash(artifact) if artifact else snapshot,
            "tarball_path": str(artifact) if artifact else None}


def record_install(workspace: Path, captured: dict[str, Any]) -> Path:
    """Publish observations after successful wiring, without claiming transactional installation."""
    workspace = workspace.resolve()
    entry = workspace / "node_modules/@jaguilar87/gaia"
    destination = entry.resolve(strict=True)
    payload = {**captured, "workspace": str(workspace), "destination": str(destination),
               "installed_hash": source_snapshot_hash(destination)}
    path = provenance_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"refusing redirected provenance record: {path}")
    if path.exists():
        prior = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(prior, dict) or prior.get("schema") != 1 or prior.get("workspace") != str(workspace):
            raise ValueError(f"unowned provenance record: {path}")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name, suffix=".pending", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def inspect_install(workspace: Path, dependency_spec: str | None) -> dict[str, Any] | None:
    """Compare a recorded installation with current source, artifact, spec and destination."""
    workspace = workspace.resolve()
    marker = provenance_path(workspace)
    if not marker.exists() and not marker.is_symlink():
        return None
    diagnostics: list[str] = []
    try:
        if marker.is_symlink():
            raise ValueError("redirected provenance record")
        data = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != 1:
            raise ValueError("unknown provenance schema")
        for key in ("workspace", "source_path", "destination", "source_hash", "installed_hash", "tarball_hash"):
            if not isinstance(data.get(key), str) or not data[key]:
                raise ValueError(f"missing or invalid provenance field: {key}")
        for key in ("source_path", "destination", "workspace"):
            if not Path(data[key]).is_absolute():
                raise ValueError(f"non-absolute provenance path: {key}")
        kind = data.get("tarball_hash_kind")
        if kind not in ("tarball", "source-snapshot"):
            raise ValueError("unknown provenance hash kind")
        if data.get("workspace") != str(workspace):
            raise ValueError("provenance belongs to a different consumer")
        if kind == "tarball" and (not isinstance(data.get("tarball_path"), str)
                                  or not Path(data["tarball_path"]).is_absolute()):
            raise ValueError("missing or invalid tarball path")
    except (OSError, ValueError, RuntimeError) as exc:
        return {"diagnostics": [f"invalid provenance: {exc}"]}

    source = Path(data["source_path"])
    try:
        if source.resolve(strict=True) != source:
            diagnostics.append("source path diverged")
        current_git = git_metadata(source)
        for key in ("commit", "branch", "dirty"):
            if data.get(key) is None or current_git[key] is None:
                diagnostics.append(f"source {key} unavailable")
            elif current_git[key] != data[key]:
                diagnostics.append(f"source {key} diverged")
        if source_snapshot_hash(source) != data["source_hash"]:
            diagnostics.append("source content/hash diverged")
    except (OSError, ValueError, RuntimeError) as exc:
        diagnostics.append(f"source unavailable: {exc}")

    expected = Path(data["tarball_path"]) if kind == "tarball" else source
    if not dependency_spec or not dependency_spec.startswith(("file:", "link:")):
        diagnostics.append("installed dependency spec diverged")
    else:
        try:
            if (workspace / dependency_spec.split(":", 1)[1]).resolve() != expected:
                diagnostics.append("installed dependency spec diverged")
        except (OSError, RuntimeError) as exc:
            diagnostics.append(f"installed dependency spec unavailable: {exc}")
    try:
        actual_hash = file_hash(expected) if kind == "tarball" else source_snapshot_hash(expected)
        if actual_hash != data["tarball_hash"]:
            diagnostics.append(f"{kind} hash diverged")
    except (OSError, ValueError, RuntimeError) as exc:
        diagnostics.append(f"{kind} unavailable: {exc}")

    entry = workspace / "node_modules/@jaguilar87/gaia"
    try:
        destination = entry.resolve(strict=True)
        if str(destination) != data["destination"]:
            diagnostics.append("installed destination diverged")
        if kind == "source-snapshot" and (not entry.is_symlink() or destination != source):
            diagnostics.append("source link diverged")
        if kind == "tarball" and destination == source:
            diagnostics.append("pack destination became a source link")
        if source_snapshot_hash(destination) != data["installed_hash"]:
            diagnostics.append("installed content/hash diverged")
    except (OSError, ValueError, RuntimeError) as exc:
        diagnostics.append(f"installed destination unavailable: {exc}")
    return {**data, "diagnostics": diagnostics}
