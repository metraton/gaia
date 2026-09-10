"""
gaia dev -- Fast local dev loop: pack + install + wire in one command.

Collapses today's manual 3-step loop (`npm pack` -> `npm`/`pnpm add
<tarball>` -> `gaia install --workspace <target>`) into a single non-atomic
`gaia dev [--workspace <path>] [--host <host>]` invocation, so testing a source change in a
real consumer workspace is one command: edit source, run `gaia dev`,
restart Claude Code, test.

Two modes:

  --mode pack (default)
    1. `npm pack` the CURRENT source tree (via `_pack_helpers.pack_tarball`,
       shared with the Phase-2 `gaia release check` gate -- one pack
       primitive, not two) into a STABLE, persistent per-workspace
       directory: `gaia.paths.cache_dir() / "dev-pack" / workspace_id()`
       (see `default_pack_dest`), not a `tempfile.TemporaryDirectory()`.
       Each attempt uses a fresh retained child directory so packing cannot
       overwrite a prior rollback artifact or a foreign tarball. Existing
        workspace packages require consumer metadata and package-manager resolution.
    2. Install the tarball into the actual consumer workspace with npm or pnpm.
       The package manager owns its package entry, .bin, spec and lockfile.
    3. Wire `.claude/` and bootstrap the DB by invoking the FRESHLY
       INSTALLED copy's own `gaia install --workspace <target>` as a
       subprocess. This is deliberate, not incidental: `_install_helpers`
       resolves its `plugin_root` from wherever it is physically loaded
       from, so delegating to the installed copy (rather than importing
       `_install_helpers` in-process from this source tree) makes the
       symlinks point at the packed tarball's node_modules copy -- the
       same safeguard `bin/validate-sandbox.sh` documents (never wire a
       consumer workspace's `.claude/` back to the Gaia source repo).
    Reflects a real shippable version and reuses the exact install
    machinery a real `npm install` consumer would exercise.

  --mode link
    Uses the consumer package manager to persist a local source dependency and
    make `<workspace>/node_modules/@jaguilar87/gaia` a live source symlink,
    then wires `.claude/` in-process with this source tree's `cmd_install`
    -- which bootstraps and re-seeds global state in `~/.gaia/gaia.db` -- so
    `_install_helpers` naturally resolves `plugin_root` to THIS source
    tree. Edits under `gaia/`, `hooks/`, `agents/`, `skills/`, `config/`,
    `tools/` are visible on the next Claude Code restart with no pack step
    at all. Instant iteration; does not reflect what actually ships.

Both modes terminate in the same place: `cli.install.cmd_install`, so the
wiring logic (settings.json, permissions, hooks, symlinks, plugin-registry,
DB bootstrap) is never duplicated between them or against `gaia install`.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# bin/cli/dev.py -> bin/cli -> bin -> gaia/ (this source tree's root)
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent

if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from cli import _converge  # type: ignore  # noqa: E402
from cli import _pack_helpers  # type: ignore  # noqa: E402
from cli import install as install_mod  # type: ignore  # noqa: E402
from cli._pack_helpers import _is_source_checkout  # type: ignore  # noqa: E402
from cli.install import _report_step  # type: ignore  # noqa: E402

_NPM_PACKAGE_NAME = "@jaguilar87/gaia"


def _restart_warning(host: str = install_mod.ALL_HOSTS) -> str:
    """The mandatory post-`gaia dev` restart notice for the configured hosts.

    The Claude Code harness pins each hook's command at SESSION START and does
    not hot-reload it, so a session that is already open keeps running the OLD
    hooks until it is restarted -- a freshly installed fix is inert until then.
    Emitted verbatim by both pack and link modes so the notice is identical and
    testable.

    Accumulative over hosts: `--host all` warns about every host it configured,
    since dropping one host's notice leaves that host silently running the old
    code. Takes the raw `--host` value and expands it here, so a caller cannot
    pass `all` through and get one host's notice.
    """
    notices = [
        "  Restart OpenCode to activate the Gaia plugin and agent configuration."
        if h == "opencode"
        else (
            "  ⚠  Restart your Claude Code session to activate the new hooks.\n"
            "     The harness pins hook commands at session start (no hot-reload),\n"
            "     so until you restart, this session keeps running the OLD hooks."
        )
        for h in install_mod.resolve_hosts(host)
    ]
    return "\n".join(notices)


# ---------------------------------------------------------------------------
# Package-manager detection + tarball install (pack mode)
# ---------------------------------------------------------------------------

def detect_package_manager(workspace: Path) -> str:
    """Return "pnpm" when the workspace is pnpm-managed, else "npm".

    Detected by the presence of a pnpm lockfile or workspace manifest --
    the same signal a developer would use to pick the right add command by
    hand. Defaults to npm, the safe universal fallback.
    """
    if (workspace / "pnpm-lock.yaml").is_file() or (workspace / "pnpm-workspace.yaml").is_file():
        return "pnpm"
    return "npm"


def _dependency_section(manifest: dict) -> str | None:
    """Identify the single consumer declaration of Gaia without moving dependency types."""
    sections = [name for name in ("dependencies", "devDependencies", "optionalDependencies")
                if isinstance(manifest.get(name), dict) and _NPM_PACKAGE_NAME in manifest[name]]
    if len(sections) > 1:
        raise ValueError("Gaia appears in multiple dependency sections")
    return sections[0] if sections else None


def _dependency_flag(package_manager: str, section: str) -> str:
    """Return the manager-native flag that preserves a dependency section."""
    flags = {
        "npm": {
            "dependencies": "--save-prod",
            "devDependencies": "--save-dev",
            "optionalDependencies": "--save-optional",
        },
        "pnpm": {
            "dependencies": "-P",
            "devDependencies": "-D",
            "optionalDependencies": "-O",
        },
    }
    return flags[package_manager][section]


def existing_package_error(workspace: Path, *, allow_source_links: bool = False,
                           legacy_source_root: Path | None = None) -> str | None:
    """Accept a normal declared npm/pnpm package only when its resolver agrees.

    No receipt or content-tree hash is used: Python caches and other runtime
    files are not installation identity. A source link also needs a matching
    local dependency declaration, source-checkout markers and resolver proof.
    """
    package = workspace / "node_modules" / "@jaguilar87" / "gaia"
    for parent in (workspace / "node_modules", package.parent):
        if parent.is_symlink():
            return f"refusing redirected package parent: {parent}"
    if not package.exists() and not package.is_symlink():
        return None
    try:
        target = package.resolve(strict=True)
        legacy_source = (package.is_symlink() and legacy_source_root is not None
                         and target == legacy_source_root.resolve() and _is_source_checkout(target))
        manifest_path = workspace / "package.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        if not isinstance(manifest, dict):
            raise ValueError("consumer package.json must be an object")
        section = _dependency_section(manifest)
        if not legacy_source and (section is None or not isinstance(manifest[section][_NPM_PACKAGE_NAME], str) or not manifest[section][_NPM_PACKAGE_NAME]):
            raise ValueError("existing package is not declared by the consumer")
        pm = detect_package_manager(workspace)
        target = package.resolve(strict=True)
        if package.is_symlink():
            store = workspace / "node_modules/.pnpm"
            spec = manifest[section][_NPM_PACKAGE_NAME] if section else ""
            if not isinstance(spec, str):
                raise ValueError("invalid source dependency declaration")
            source_spec = spec.split(":", 1)[1] if spec.startswith(("file:", "link:")) else None
            declared_source = (workspace / source_spec).resolve() if source_spec else None
            source_link = allow_source_links and declared_source == target and _is_source_checkout(target)
            if not source_link and not legacy_source:
                if pm != "pnpm" or store.is_symlink():
                    raise ValueError("unowned source or foreign package symlink")
                target.relative_to(store.resolve())
        identity = json.loads((target / "package.json").read_text())
        if not isinstance(identity, dict) or identity.get("name") != _NPM_PACKAGE_NAME or not (target / "bin/gaia").is_file():
            raise ValueError("installed package identity or entrypoint is missing")
        if legacy_source and not source_link:
            return None
        command = [pm, "list" if pm == "pnpm" else "ls", "--json", "--depth=0", "--long"]
        result = subprocess.run(command, cwd=str(workspace), capture_output=True,
                                text=True, check=False, timeout=30)
        if result.returncode != 0:
            raise ValueError(f"{pm} dependency resolution failed (exit {result.returncode})")
        resolved = json.loads(result.stdout)
        if pm == "pnpm":
            if not isinstance(resolved, list) or len(resolved) != 1:
                raise ValueError("pnpm resolution must identify exactly one consumer")
            resolved = resolved[0]
        resolution_section = section if pm == "pnpm" else "dependencies"
        if not isinstance(resolved, dict) or not isinstance(resolved.get(resolution_section), dict):
            raise ValueError("package manager did not resolve the declared dependency")
        if (not isinstance(resolved.get("path"), str)
                or not Path(resolved["path"]).is_absolute()
                or Path(resolved["path"]).resolve() != workspace.resolve()):
            raise ValueError("package manager resolved a different consumer")
        dependency = resolved[resolution_section].get(_NPM_PACKAGE_NAME)
        if not isinstance(dependency, dict) or not isinstance(dependency.get("path"), str):
            raise ValueError("package manager did not provide the installed package path")
        reported = Path(dependency["path"])
        if not reported.is_absolute() or reported.resolve(strict=True) != target:
            raise ValueError("package-manager resolution disagrees with the installed package")
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return f"existing Gaia package left untouched: {exc}"
    return None


def install_tarball(
    workspace: Path,
    tarball: Path,
    *,
    package_manager: str | None = None,
    timeout: int = 300,
    source_link: bool = False,
) -> dict[str, Any]:
    """Install a tarball or explicit source directory through the consumer manager.

    Mirrors `bin/validate-sandbox.sh`'s `install_package()`: if the
    workspace has no package.json yet, create a minimal one first so the
    package manager has an anchor to install against.
    """
    workspace = Path(workspace).resolve()
    pm = package_manager or detect_package_manager(workspace)
    try:
        manifest_path = workspace / "package.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        section = _dependency_section(manifest) or "dependencies"
    except (OSError, ValueError, AttributeError) as exc:
        return {"action": "error", "path": str(workspace), "details": str(exc), "package_manager": pm}
    if pm not in ("npm", "pnpm"):
        return {"action": "error", "path": str(workspace),
                "details": f"unsupported package manager: {pm}", "package_manager": pm}
    section_flag = _dependency_flag(pm, section)

    if not (workspace / "package.json").is_file():
        try:
            anchor = subprocess.run(
                ["npm", "init", "-y"],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "action": "error",
                "path": str(workspace / "package.json"),
                "details": f"failed to create anchor package.json: {exc}",
                "package_manager": pm,
            }
        if anchor.returncode != 0:
            return {
                "action": "error",
                "path": str(workspace / "package.json"),
                "details": f"npm init exited {anchor.returncode}; package installation not attempted",
                "package_manager": pm,
            }

    if pm == "pnpm":
        commands = [["pnpm", "add", section_flag, str(tarball)]]
        if source_link:
            commands.append(["pnpm", "link", str(tarball)])
    else:
        cmd = ["npm", "install", "--no-audit", "--no-fund", section_flag]
        if source_link:
            cmd.append("--install-links=false")
        cmd.append(str(tarball))
        commands = [cmd]

    for command in commands:
        try:
            result = subprocess.run(
                command,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "action": "error",
                "path": str(workspace),
                "details": f"{pm} {command[1]} failed to invoke: {exc}",
                "package_manager": pm,
            }

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown error").strip()[-500:]
            return {
                "action": "error",
                "path": str(workspace),
                "details": f"{pm} {command[1]} exited {result.returncode}: {detail}",
                "package_manager": pm,
            }

    package = workspace / "node_modules/@jaguilar87/gaia"
    if not source_link and package.is_symlink() and _is_source_checkout(package.resolve()):
        return {"action": "error", "path": str(package), "details": "package manager retained a source link in pack mode",
                "package_manager": pm}

    return {
        "action": "created",
        "path": str(workspace / "node_modules" / "@jaguilar87" / "gaia"),
        "details": f"installed {tarball.name} via {pm}",
        "package_manager": pm,
    }


def wire_workspace_via_installed_gaia(
    workspace: Path,
    *,
    quiet: bool = True,
    timeout: int = 120,
    host: str = install_mod.ALL_HOSTS,
) -> dict[str, Any]:
    """Run the FRESHLY INSTALLED copy's own `gaia install --workspace`.

    Deliberately delegates to `<workspace>/node_modules/@jaguilar87/gaia/bin/gaia`
    rather than importing `_install_helpers` from this source tree in-process
    -- see the module docstring for why plugin_root must resolve to the
    installed copy, not this dev source tree.
    """
    install_mod.resolve_hosts(host)
    installed_gaia = (
        workspace / "node_modules" / "@jaguilar87" / "gaia" / "bin" / "gaia"
    )
    if not installed_gaia.is_file():
        return {
            "action": "error",
            "path": str(installed_gaia),
            "details": "installed gaia entrypoint not found -- tarball install may have failed",
        }

    cmd = [
        sys.executable or "python3",
        str(installed_gaia),
        "install",
        "--workspace",
        str(workspace),
        "--host",
        host,
        "--no-path",
        "--strict-wiring",
    ]
    if quiet:
        cmd.append("--quiet")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(workspace),
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "action": "error",
            "path": str(installed_gaia),
            "details": f"gaia install invocation failed: {exc}",
        }

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()[-500:]
        return {
            "action": "error",
            "path": str(installed_gaia),
            "details": f"gaia install exited {result.returncode}: {detail}",
        }

    tail = (result.stdout or "").strip().splitlines()
    return {
        "action": "created",
        "path": str(workspace / ".claude"),
        "details": tail[-1] if tail else "workspace wired",
    }


# ---------------------------------------------------------------------------
# Link mode: persist and link this source through the consumer manager
# ---------------------------------------------------------------------------

def install_source_link(workspace: Path, source_root: Path) -> dict[str, Any]:
    """Save a native local-directory dependency, then prove it is a live source link."""
    source = source_root.resolve()
    workspace = workspace.resolve()
    package = workspace / "node_modules/@jaguilar87/gaia"
    error = {"action": "error", "path": str(package)}
    if source.is_relative_to(workspace) or workspace.is_relative_to(source):
        return {**error, "details": "source and consumer must be separate non-overlapping directories"}
    if not _is_source_checkout(source):
        return {**error, "details": "source is not a Gaia checkout"}
    try:
        source_identity = json.loads((source / "package.json").read_text())
        if source_identity.get("name") != _NPM_PACKAGE_NAME or not (source / "bin/gaia").is_file():
            return {**error, "details": "source package identity or entrypoint does not match Gaia"}
    except (OSError, ValueError, AttributeError) as exc:
        return {**error, "details": f"invalid source identity: {exc}"}
    ownership = existing_package_error(workspace, allow_source_links=True, legacy_source_root=source)
    if ownership:
        return {**error, "details": ownership}
    try:
        parent = default_pack_dest(workspace)
        parent.mkdir(parents=True, exist_ok=True)
        recovery_path = Path(tempfile.mkdtemp(prefix="link-attempt-", dir=parent)) / "recovery.json"
        before = consumer_recovery_state(workspace)
        path = workspace / "package.json"
        manifest = json.loads(path.read_text()) if path.exists() else {}
        section = _dependency_section(manifest) or "dependencies"
        write_recovery_evidence(recovery_path, workspace, before, "before-install", failed=False)
    except (OSError, ValueError, RuntimeError, AttributeError) as exc:
        return {**error, "details": f"cannot preserve source-switch recovery evidence: {exc}"}
    result = install_tarball(workspace, source, source_link=True)
    if result["action"] not in ("created", "updated", "noop"):
        write_recovery_evidence(recovery_path, workspace, before, "source-install", failed=True)
        return {**error, "details": f"{result['details']}; recovery required, no rollback; evidence: {recovery_path}"}
    try:
        after = json.loads((workspace / "package.json").read_text())
        actual_section = _dependency_section(after)
        spec = after.get(section, {}).get(_NPM_PACKAGE_NAME, "")
        if actual_section != section or not isinstance(spec, str) or not spec.startswith(("file:", "link:")):
            raise ValueError("package manager did not save a local dependency in the original section")
        if (workspace / spec.split(":", 1)[1]).resolve() != source:
            raise ValueError("saved local dependency does not identify selected source")
        if not package.is_symlink() or package.resolve(strict=True) != source:
            raise ValueError("package manager did not create a live source symlink")
        ownership = existing_package_error(workspace, allow_source_links=True)
        if ownership:
            raise ValueError(ownership)
    except (OSError, ValueError, RuntimeError, AttributeError) as exc:
        write_recovery_evidence(recovery_path, workspace, before, "source-verify", failed=True)
        return {**error, "details": f"{exc}; recovery required, no rollback; evidence: {recovery_path}"}
    return {"action": "created", "path": str(package), "details": f"consumer linked to {source}",
            "recovery_path": recovery_path, "before": before}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _run_link_mode(workspace: Path, *, quiet: bool, verbose: bool, host: str) -> int:
    """Wire source only after a successful, ownership-safe link."""
    install_mod.resolve_hosts(host)
    link_res = install_source_link(workspace, _PACKAGE_ROOT)
    _report_step(name="node_modules link", result=link_res, quiet=quiet, verbose=verbose)
    if link_res["action"] not in ("created", "noop"):
        print(f"gaia dev: {link_res['details']}", file=sys.stderr)
        return 1

    ns = argparse.Namespace(
        postinstall=False,
        quiet=quiet,
        verbose=verbose,
        db_path=None,
        workspace=str(workspace),
        skip_workspace=False,
        no_path=True,
        strict_wiring=True,
        host=host,
    )
    rc = install_mod.cmd_install(ns)
    if "recovery_path" in link_res:
        write_recovery_evidence(link_res["recovery_path"], workspace, link_res["before"],
                                "complete" if rc == 0 else "source-wire", failed=rc != 0)
        if rc != 0:
            print(f"gaia dev: wiring failed; recovery required, no rollback performed; evidence: {link_res['recovery_path']}", file=sys.stderr)
    if rc == 0 and not quiet:
        print(
            "\n  gaia dev (link): workspace wired to the live source tree.\n"
        )
        print(_restart_warning(host))
        print()
    return rc


def default_pack_dest(workspace: Path) -> Path:
    """Return the stable, persistent pack destination for *workspace*.

    ``cache_dir() / "dev-pack" / workspace_id(workspace)`` -- a pure
    function of the workspace path and the environment's `GAIA_DATA_DIR`
    (via `gaia.paths.cache_dir`), so repeated calls for the same workspace
    under the same data dir always resolve to the same directory. This
    replaces the old `tempfile.TemporaryDirectory()` default: that
    directory (and everything in it, including the packed tarball) was
    deleted before `gaia dev` even returned, but the tarball's path is
    also what the consumer workspace's `package.json`/`pnpm-lock.yaml`
    record as a `file:` dependency -- so the very next `pnpm install`
    (e.g. a routine lockfile refresh) failed with ENOENT because the
    referenced path no longer existed. A stable, persistent destination
    retains each attempt in its own child directory; previous file references
    are never auto-deleted or overwritten by a subsequent attempt.
    """
    from gaia.paths import cache_dir, workspace_id

    return cache_dir() / "dev-pack" / workspace_id(cwd=workspace)


def content_address_tarball(tarball: Path) -> Path:
    """Rename *tarball* to ``<stem>+<sha8>.tgz`` in place (same directory).

    Inserts a content hash of the tarball's bytes before the ``.tgz``
    extension: ``jaguilar87-gaia-5.1.1.tgz`` -> ``jaguilar87-gaia-5.1.1+<sha8>.tgz``.
    Because pnpm keys a ``file:`` dependency's virtual-store entry by the
    spec PATH (not by content), a changed-content repack at the same version
    now yields a NEW filename -> a NEW store key -> a forced fresh
    extraction. Identical content yields the identical name (idempotent, no
    churn). The tarball's INTERNAL ``package.json`` version is never touched.

    Returns the new path. If the derived name already exists (same content
    re-packed), the freshly packed tarball replaces it atomically.
    """
    sha8 = _pack_helpers.content_hash8(tarball)
    name = tarball.name
    stem = name[:-4] if name.endswith(".tgz") else name
    # Guard against double-suffixing if a hashed tarball is ever re-fed in.
    if "+" in stem:
        stem = stem.split("+", 1)[0]
    new_path = tarball.with_name(f"{stem}+{sha8}.tgz")
    if new_path != tarball:
        os.replace(tarball, new_path)  # atomic within the same directory
    return new_path


def prune_sibling_tarballs(keep: Path) -> list[str]:
    """Retain artifacts until ownership and rollback references can be proven.

    A filename or shared pack directory does not establish ownership, and the
    previous package may still need its tarball even after successful wiring.
    Kept as a no-op for callers of the former destructive cleanup helper.
    """
    return []


def consumer_recovery_state(workspace: Path) -> dict[str, Any]:
    """Capture bounded recovery evidence, not an authorization to overwrite files."""
    files = {}
    for name in ("package.json", "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml"):
        path = workspace / name
        if path.is_symlink():
            raise ValueError(f"refusing redirected consumer metadata: {path}")
        data = path.read_bytes() if path.exists() else None
        files[name] = {
            "bytes_base64": base64.b64encode(data).decode() if data is not None else None,
            "sha256": hashlib.sha256(data).hexdigest() if data is not None else None,
        }
    package = workspace / "node_modules/@jaguilar87/gaia"
    identity = package / "package.json"
    return {
        "files": files,
        "package": {
            "path": str(package),
            "link": os.readlink(package) if package.is_symlink() else None,
            "target": str(package.resolve()),
            "present": package.exists(),
            "identity": identity.read_text() if identity.is_file() else None,
        },
    }


def write_recovery_evidence(path: Path, workspace: Path, before: dict, stage: str,
                            *, failed: bool) -> None:
    """Retain original metadata plus observed changes without pretending they are ours."""
    try:
        after = consumer_recovery_state(workspace)
    except (OSError, ValueError, RuntimeError) as exc:
        after = {"unreadable": str(exc)}
    payload = {
        "workspace": str(workspace), "stage": stage,
        "status": "recovery-required" if failed else ("completed" if stage == "complete" else "prepared"),
        "before": before, "observed_after": after,
        "rollback": "not performed; observed changes may include concurrent or foreign writes",
    }
    temporary = path.with_suffix(".pending")
    try:
        temporary.write_text(json.dumps(payload, indent=2) + "\n")
        os.replace(temporary, path)
    except OSError as exc:
        if stage == "before-install":
            raise
        print(f"gaia dev: cannot update recovery evidence {path}: {exc}; retain prior evidence and inspect consumer state", file=sys.stderr)


def rewrite_workspace_dep_spec(workspace: Path, tarball: Path) -> dict[str, Any]:
    """Verify that the package manager saved the selected local artifact."""
    pkg_path = workspace / "package.json"
    try:
        if not pkg_path.is_file():
            return {"action": "error", "path": str(pkg_path), "details": "package manager did not save package.json"}
        data = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"action": "error", "path": str(pkg_path), "details": f"could not read package.json: {exc}"}

    if not isinstance(data, dict):
        return {"action": "error", "path": str(pkg_path), "details": "package.json must be an object"}
    try:
        section = _dependency_section(data)
    except ValueError as exc:
        return {"action": "error", "path": str(pkg_path), "details": str(exc)}
    if section is None:
        return {"action": "error", "path": str(pkg_path), "details": "package manager did not save Gaia dependency"}
    spec = data[section][_NPM_PACKAGE_NAME]
    try:
        if not isinstance(spec, str) or not spec.startswith("file:"):
            raise ValueError("saved Gaia dependency is not a file: reference")
        if (workspace / spec[5:]).resolve() != tarball.resolve():
            raise ValueError("saved Gaia dependency identifies a different artifact")
    except (OSError, ValueError) as exc:
        return {"action": "error", "path": str(pkg_path), "details": str(exc)}
    return {"action": "noop", "path": str(pkg_path),
            "details": f"package manager saved {section} reference {spec}"}


def record_dev_build(version: str | None) -> str | None:
    """Advance the local dev-build counter for *version* and return its label.

    The counter is keyed by base version and advanced only when the hooks
    tree's content digest changed since the last recorded build, so a repack
    whose packaged bytes are identical does not inflate it -- see
    `gaia.dev_builds` for why that is the right identity. The digest is taken
    from THIS source tree's `hooks/`, which is exactly what was just packed.

    Returns a label like `5.3.0 (dev.7, build fb27693c)`, or None when there is
    nothing to report (no version, unavailable module, unwritable sidecar).
    Never raises: the counter is a display affordance and must not be able to
    fail the dev loop it annotates.
    """
    if not version:
        return None
    try:
        from gaia.dev_builds import format_label, record_build  # noqa: PLC0415
        from gaia.hooks_build import hooks_content_hash  # noqa: PLC0415

        record = record_build(version, hooks_content_hash(_PACKAGE_ROOT / "hooks"))
        return format_label(version, record) if record else None
    except Exception:
        return None


def _print_convergence_report(workspace: Path, origin_version: str | None, quiet: bool) -> dict:
    """Inspect + report the 5 install surfaces of *workspace* vs this origin.

    A thin `gaia dev` adapter over the shared convergence driver (see
    `cli/_converge.run_convergence_report`): it resolves the dev-side origin
    inputs -- `EXPECTED_SCHEMA_VERSION` (code's schema expectation), the DB path,
    and the global-npm bin dir -- then hands off to the SAME inspect+format+
    degrade path `gaia release` uses, so the convergence classification is
    written once, not duplicated between the two commands. Best-effort and
    read-only: never raises, prints only when not quiet, returns the raw report.
    """
    from cli.doctor import EXPECTED_SCHEMA_VERSION  # noqa: PLC0415

    return _converge.run_convergence_report(
        workspace,
        origin_version=origin_version,
        expected_version=EXPECTED_SCHEMA_VERSION,
        db_path=_converge.default_db_path(),
        npm_global_bin=install_mod._npm_global_prefix(),
        quiet=quiet,
    )


def _run_pack_mode(
    workspace: Path,
    *,
    quiet: bool,
    verbose: bool,
    keep_tarball: bool,
    pack_dest: str | None,
    no_global_link: bool = False,
    host: str = install_mod.ALL_HOSTS,
) -> int:
    """Install through the consumer package manager; report failures without fake rollback."""
    # keep_tarball is retained for CLI compatibility only: now that the
    # pack destination is always stable and persistent (never a tmp dir
    # cleaned up on exit), there is nothing left to delete, so the flag is
    # a no-op.
    del keep_tarball, no_global_link
    install_mod.resolve_hosts(host)

    ownership_error = existing_package_error(workspace, allow_source_links=True, legacy_source_root=_PACKAGE_ROOT)
    if ownership_error:
        print(f"gaia dev: {ownership_error}", file=sys.stderr)
        return 1

    pack_parent = (
        Path(pack_dest).expanduser().resolve()
        if pack_dest
        else default_pack_dest(workspace)
    )
    try:
        pack_parent.mkdir(parents=True, exist_ok=True)
        dest_dir = Path(tempfile.mkdtemp(prefix="attempt-", dir=pack_parent))
    except OSError as exc:
        print(f"gaia dev: cannot prepare retained pack directory: {exc}", file=sys.stderr)
        return 1

    pack_res = _pack_helpers.pack_tarball(_PACKAGE_ROOT, dest_dir=dest_dir)
    _report_step(name="npm pack", result=pack_res, quiet=quiet, verbose=verbose)
    if pack_res["action"] not in ("created", "updated", "noop"):
        return 1

    # Content-address the packed tarball's FILENAME so a same-version repack
    # with changed content gets a new pnpm store key -> a forced fresh
    # extraction (the packaged version stays clean). See
    # content_address_tarball for the mechanism.
    tarball = content_address_tarball(Path(pack_res["tarball"]))
    _report_step(
        name="content-address tarball",
        result={"action": "created", "path": str(tarball), "details": f"-> {tarball.name}"},
        quiet=quiet,
        verbose=verbose,
    )

    recovery_path = dest_dir / "recovery.json"
    try:
        before = consumer_recovery_state(workspace)
        write_recovery_evidence(recovery_path, workspace, before, "before-install", failed=False)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"gaia dev: cannot preserve pre-install recovery evidence: {exc}", file=sys.stderr)
        return 1

    install_res = install_tarball(workspace, tarball)
    pm = install_res.get("package_manager", "npm")
    _report_step(name=f"{pm} install", result=install_res, quiet=quiet, verbose=verbose)
    if install_res["action"] not in ("created", "updated", "noop"):
        write_recovery_evidence(recovery_path, workspace, before, "install", failed=True)
        print(f"gaia dev: recovery evidence retained at {recovery_path}", file=sys.stderr)
        print("gaia dev: package installation failed; consumer state may be partially changed; no rollback performed", file=sys.stderr)
        return 1

    spec_res = rewrite_workspace_dep_spec(workspace, tarball)
    _report_step(name="package.json spec", result=spec_res, quiet=quiet, verbose=verbose)
    if spec_res["action"] not in ("created", "updated", "noop"):
        write_recovery_evidence(recovery_path, workspace, before, "spec", failed=True)
        print(f"gaia dev: recovery evidence retained at {recovery_path}", file=sys.stderr)
        print("gaia dev: dependency spec update failed after installation; no rollback performed", file=sys.stderr)
        return 1

    wire_res = wire_workspace_via_installed_gaia(workspace, quiet=quiet, host=host)
    _report_step(name="gaia install (wire)", result=wire_res, quiet=quiet, verbose=verbose)
    if wire_res["action"] not in ("created", "updated", "noop"):
        write_recovery_evidence(recovery_path, workspace, before, "wire", failed=True)
        print(f"gaia dev: recovery evidence retained at {recovery_path}", file=sys.stderr)
        print("gaia dev: wiring failed after installation; package and host state may be partially changed; no rollback performed", file=sys.stderr)
        return 1

    write_recovery_evidence(recovery_path, workspace, before, "complete", failed=False)

    # READ half of the shared convergence routine: confirm the destination's 5
    # surfaces converged on this origin (the local source). Read-only.
    _print_convergence_report(workspace, pack_res.get("version"), quiet=quiet)

    # Counted only now that the build actually landed, so a run that failed to
    # pack, install, or wire never advances the iteration count.
    version = pack_res.get("version")
    build_label = record_dev_build(version) or version

    if not quiet:
        print(
            f"\n  gaia dev: packed {pack_res.get('name')}@{build_label} "
            f"into {workspace}.\n"
        )
        print(_restart_warning(host))
        print()
    return 0


# ---------------------------------------------------------------------------
# Plugin interface
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the 'dev' subcommand."""
    p = subparsers.add_parser(
        "dev",
        help="Fast local dev loop: pack/link + install + wire in one command",
        description=(
            "Collapse the manual pack+add+install loop into one command.\n"
            "\n"
            "  --mode pack (default): npm pack this source tree into a stable,\n"
            "  persistent per-workspace path (default_pack_dest, override with\n"
            "  --pack-dest), install into the actual consumer with npm/pnpm,\n"
            "  update its Gaia dependency spec, then wire hosts + bootstrap DB\n"
            "  via the freshly installed copy's own `gaia install`. Each pack\n"
            "  attempt is retained separately. Repeated normal installations\n"
            "  require matching consumer metadata and package-manager resolution.\n"
            "  Declared source links and installed packages switch via the consumer\n"
            "  manager. Unknown entries are refused; legacy links to this exact\n"
            "  source checkout can be saved as normal local dependencies.\n"
            "  Package-manager and wiring failures may leave partial changes;\n"
            "  no transactional rollback is claimed.\n"
            "\n"
            "  --mode link: npm install --install-links=false <source>, or pnpm\n"
            "  add <source> followed by pnpm link <source>, saves local metadata\n"
            "  and leaves node_modules as a live source symlink.\n"
            "  Source must be outside the consumer. It skips the PACK only --\n"
            "  it still runs the full `gaia install`, which bootstraps and\n"
            "  RE-SEEDS global state in ~/.gaia/gaia.db (schema migrations,\n"
            "  contract permissions, surface routing) and wires the workspace.\n"
            "\n"
            "Use --host=opencode to configure OpenCode, or --host=all to configure\n"
            "every supported host in one run (the default). No global npm link\n"
            "or PATH launcher is changed by dev. --link abbreviates --mode link.\n"
            "global install steps run once regardless of how many hosts are wired.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--workspace",
        dest="workspace",
        type=str,
        default=None,
        help="Target workspace (default: INIT_CWD, then cwd)",
    )
    p.add_argument(
        "--host",
        choices=install_mod.HOST_CHOICES,
        default=install_mod.ALL_HOSTS,
        help=(
            "Host to configure, or `all` for every supported host in one run "
            "(default: all). Forwarded to `gaia install`, which runs "
            "the global steps once and repeats only the per-host wiring."
        ),
    )
    p.add_argument(
        "--mode",
        dest="mode",
        choices=["pack", "link"],
        default=None,
        help="pack (default): npm pack + install + wire. link: symlink source for instant iteration.",
    )
    p.add_argument("--link", action="store_true", help="Use link mode; conflicts with --mode pack")
    p.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress informational output; only errors print",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Show noop steps too (default: only changes are printed)",
    )
    p.add_argument(
        "--keep-tarball",
        dest="keep_tarball",
        action="store_true",
        default=False,
        help=(
            "Deprecated, kept for compatibility: the packed tarball now always "
            "persists at a stable per-workspace location, so this is a no-op "
            "(ignored in --mode link)"
        ),
    )
    p.add_argument(
        "--pack-dest",
        dest="pack_dest",
        type=str,
        default=None,
        help=(
            "Directory to write the packed tarball into (default: a stable, "
            "persistent path under gaia.paths.cache_dir(), keyed by the "
            "workspace's identity, with a fresh retained attempt subdirectory)"
        ),
    )
    p.add_argument(
        "--no-global-link",
        dest="no_global_link",
        action="store_true",
        default=False,
        help=(
            "Compatibility no-op: dev always leaves the global npm alias untouched"
        ),
    )
    return p


def _refuse_non_source_checkout_message() -> str:
    """Fail-loud message when `gaia dev` is invoked from a non-source copy.

    `gaia dev` packs and reinstalls the tree it is physically loaded from
    (`_PACKAGE_ROOT`, see `wire_workspace_via_installed_gaia`'s docstring for
    why that matters). Running it from an installed copy (npm registry
    install, or a workspace's `.claude/`-wired symlink) would pack THAT slim
    copy instead of real source -- silently shipping nothing useful. There is
    no env-var escape hatch: the caller must invoke the actual checkout.
    """
    return (
        f"gaia dev: {_PACKAGE_ROOT} is not a Gaia SOURCE checkout "
        "(missing build/gaia.manifest.json or tests/).\n"
        "`gaia dev` packs and reinstalls the CURRENT source tree, so it must be "
        "run from the source checkout itself, not an installed copy.\n"
        "Run it as: python3 <checkout>/bin/gaia dev [--workspace <path>]"
    )


def cmd_dev(args: argparse.Namespace) -> int:
    """Execute the dev subcommand."""
    if not _is_source_checkout(_PACKAGE_ROOT):
        print(_refuse_non_source_checkout_message(), file=sys.stderr)
        return 1

    quiet = bool(getattr(args, "quiet", False))
    verbose = bool(getattr(args, "verbose", False))
    explicit_mode = getattr(args, "mode", None)
    link = bool(getattr(args, "link", False))
    if explicit_mode not in (None, "pack", "link") or (link and explicit_mode == "pack"):
        print("gaia dev: invalid mode or contradictory --link and --mode pack", file=sys.stderr)
        return 1
    mode = "link" if link else (explicit_mode or "pack")
    keep_tarball = bool(getattr(args, "keep_tarball", False))
    pack_dest = getattr(args, "pack_dest", None)
    no_global_link = bool(getattr(args, "no_global_link", False))
    workspace_arg = getattr(args, "workspace", None)
    host = getattr(args, "host", install_mod.ALL_HOSTS)
    try:
        install_mod.resolve_hosts(host)
    except ValueError as exc:
        print(f"gaia dev: {exc}", file=sys.stderr)
        return 1

    workspace = (
        Path(workspace_arg).expanduser().resolve()
        if workspace_arg
        else Path(os.environ.get("INIT_CWD", os.getcwd())).resolve()
    )

    if not workspace.is_dir():
        print(f"gaia dev: workspace {workspace} is not an existing directory", file=sys.stderr)
        return 1

    if not quiet:
        print(f"\n  gaia dev ({mode} mode)")
        print(f"  source:    {_PACKAGE_ROOT}")
        print(f"  workspace: {workspace}\n")

    if mode == "link":
        return _run_link_mode(workspace, quiet=quiet, verbose=verbose, host=host)

    return _run_pack_mode(
        workspace,
        quiet=quiet,
        verbose=verbose,
        keep_tarball=keep_tarball,
        pack_dest=pack_dest,
        no_global_link=no_global_link,
        host=host,
    )
