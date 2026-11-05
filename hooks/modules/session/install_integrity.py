"""Session-start check of Gaia's install links and settings against the hashes the install manifest recorded: a notice, never a block."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MISSING = object()


def integrity_notice(workspace: Path) -> str:
    """One line naming each recorded install path that drifted, or "" when none did or the check cannot run."""
    try:
        from modules.session.plugin_upgrade import _cli_module

        manifest = _cli_module("_manifest")
        recorded = manifest.load(workspace)
        drifted = [e["path"] for e in (recorded or {}).get("entries", []) if _drifted(manifest, workspace, e)]
    except Exception as exc:  # noqa: BLE001
        logger.debug("install integrity check skipped: %s", exc)
        return ""
    if not drifted:
        return ""
    return (
        f"Gaia's install changed since it was recorded: {', '.join(drifted)}. "
        "`gaia doctor` shows the install's state; `gaia update` rewires it."
    )


def _drifted(manifest: Any, workspace: Path, entry: dict) -> bool:
    """Whether the path now differs from what the install wrote; for JSON settings only the keys Gaia wrote count."""
    written = entry["written"]
    if written["type"] in ("absent", "dir"):
        return False
    now = manifest._encode(manifest._state_of(manifest._abs(workspace, entry["path"])))
    if now["type"] != written["type"]:
        return True
    if written["type"] == "symlink":
        return now["target"] != written["target"]
    if "json_ops" not in entry:
        return now["sha256"] != written["sha256"]
    try:
        current = json.loads(base64.b64decode(now["b64"]))
    except ValueError:
        return True
    expected = json.loads(base64.b64decode(written["b64"]))
    for op in entry["json_ops"]:
        here = _at(current, op["path"])
        if op["op"] == "list" and not (isinstance(here, list) and all(i in here for i in op["added"])):
            return True
        if op["op"] in ("add", "set") and here != _at(expected, op["path"]):
            return True
    return False


def _at(doc: Any, path: list) -> Any:
    for key in path:
        if not isinstance(doc, dict) or key not in doc:
            return _MISSING
        doc = doc[key]
    return doc
