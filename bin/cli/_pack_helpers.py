"""
_pack_helpers.py -- shared packaging primitive for `gaia dev` and (Phase 2)
`gaia release check`.

``pack_tarball()`` wraps ``npm pack --json`` so any subcommand that needs a
freshly-packed tarball of the current source tree gets identical behaviour:
the ``prepack`` npm lifecycle script runs first (``npm run clean && npm run
generate:plugin-root``, see package.json), so the tarball always reflects
the CURRENT source tree -- including a freshly regenerated
``.claude-plugin/plugin.json`` + ``hooks/hooks.json`` -- never a stale
checked-in artifact.

This module holds ONLY the pack step. Consumers (``gaia dev`` today,
``gaia release check`` in Phase 2) own their own install/wire/verify
orchestration on top of the tarball path this returns -- keeping this
module's surface small and reusable rather than growing it into a second
copy of `_install_helpers.py`.

Naming convention: leading underscore, like `_install_helpers.py` --
private shared helper, not a CLI plugin (`bin/gaia`'s dispatcher imports
every `bin/cli/*.py` file but only registers modules that expose a
`register()` function; this module intentionally has none).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


def content_hash8(path: Path) -> str:
    """Return the first 8 hex chars of the SHA-256 of *path*'s bytes.

    Used to give a packed tarball a content-addressed filename
    (``jaguilar87-gaia-<version>+<sha8>.tgz``) so a SAME-version repack with
    changed content produces a NEW filename -> a NEW pnpm virtual-store key
    (pnpm keys a ``file:`` dependency's store entry by the spec PATH, not by
    content) -> a forced fresh extraction. Identical content yields an
    identical hash, so an unchanged repack does not churn the store. The
    tarball's INTERNAL ``package.json`` version is untouched -- only the
    on-disk filename carries the suffix.

    Streams the file in 64 KiB chunks; never raises for a readable file.
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


def pack_tarball(
    source_root: Path,
    *,
    dest_dir: Path | None = None,
    timeout: int = 180,
) -> dict[str, Any]:
    """Pack *source_root* into an npm tarball via ``npm pack --json``.

    Runs with ``cwd=source_root`` so npm's own lifecycle (``prepack``)
    regenerates the plugin manifests in place before packing -- the tarball
    always reflects the current working tree, not a stale commit.

    Args:
        source_root: the Gaia package root (contains package.json).
        dest_dir: directory to write the tarball into (created if missing).
            Defaults to *source_root* itself when omitted -- callers doing
            local dev loops should pass a tmp directory so the source tree
            is never littered with a `.tgz`.
        timeout: seconds to wait for `npm pack` before giving up.

    Returns:
        On success: ``{"action": "created", "path": str, "details": str,
        "tarball": Path, "name": str, "version": str}``.
        On failure: ``{"action": "error", "path": "", "details": str}``.
        Never raises -- subprocess/parse failures are captured in the
        returned dict so callers can report and exit cleanly.
    """
    source_root = Path(source_root).resolve()
    dest = Path(dest_dir).expanduser().resolve() if dest_dir else source_root

    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"action": "error", "path": "", "details": f"failed to create {dest}: {exc}"}

    cmd = ["npm", "pack", "--json", "--pack-destination", str(dest)]

    try:
        result = subprocess.run(
            cmd,
            cwd=str(source_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"action": "error", "path": "", "details": f"npm pack failed to invoke: {exc}"}

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown npm pack error").strip()[-500:]
        return {
            "action": "error",
            "path": "",
            "details": f"npm pack exited {result.returncode}: {detail}",
        }

    # `npm pack --json` prints lifecycle script output (prepack: clean +
    # generate:plugin-root) to stdout BEFORE the JSON array. Slice from the
    # first '[' so lifecycle noise never breaks the parse.
    stdout = result.stdout
    start = stdout.find("[")
    if start == -1:
        return {
            "action": "error",
            "path": "",
            "details": f"npm pack produced no JSON payload: {stdout[-300:]}",
        }

    try:
        payload = json.loads(stdout[start:])
    except json.JSONDecodeError as exc:
        return {"action": "error", "path": "", "details": f"npm pack JSON payload unparsable: {exc}"}

    if not payload:
        return {"action": "error", "path": "", "details": "npm pack returned an empty payload"}

    entry = payload[0]
    filename = entry.get("filename")
    if not filename:
        return {"action": "error", "path": "", "details": f"npm pack payload missing 'filename': {entry}"}

    tarball = dest / filename
    if not tarball.is_file():
        return {
            "action": "error",
            "path": str(tarball),
            "details": f"npm pack reported {tarball} but the file was not found",
        }

    name = entry.get("name", "")
    version = entry.get("version", "")
    return {
        "action": "created",
        "path": str(tarball),
        "details": f"packed {name}@{version} -> {tarball}",
        "tarball": tarball,
        "name": name,
        "version": version,
    }


__all__ = ["pack_tarball", "content_hash8"]
