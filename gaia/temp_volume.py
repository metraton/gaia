"""Room left on the host's temp spool and Gaia's tmp, and this platform's remedy; Gaia reports, never reconfigures."""

from __future__ import annotations

import os
import platform
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

GIB = 1024 ** 3
MIN_FREE_BYTES = 1 * GIB
MIN_MEMORY_TOTAL_BYTES = 4 * GIB
MAX_USED_FRACTION = 0.8

REMEDIES = {
    "linux": "raise the size= of its tmpfs mount (/etc/fstab or `systemctl edit tmp.mount`), "
             "or set CLAUDE_CODE_TMPDIR to a folder on a larger disk",
    "wsl": "raise the size= of the /tmp line in the distro's /etc/fstab, "
           "or set CLAUDE_CODE_TMPDIR to a folder on the distro disk",
    "macos": "free space on that volume, or set CLAUDE_CODE_TMPDIR to a folder on a larger one",
    "windows": "free space on the drive holding %TEMP% (Storage Sense, Disk Cleanup), "
               "or set CLAUDE_CODE_TMPDIR to a folder on a larger drive",
    "container": "give the container a larger /tmp (a volume, or --tmpfs /tmp:size=...), "
                 "or set CLAUDE_CODE_TMPDIR to a mounted volume",
}


def platform_name() -> str:
    """``linux``, ``wsl``, ``macos``, ``windows`` or ``container``."""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return "container"
    if "microsoft" in platform.release().lower():
        return "wsl"
    return "linux"


def host_spool_dir() -> Path:
    """The host's temp folder: CLAUDE_CODE_TMPDIR, else its default; a dispatched shell's TMPDIR is not it."""
    override = os.environ.get("CLAUDE_CODE_TMPDIR")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path("/tmp")
    from gaia.paths import tmp_dir

    tmpdir = os.environ.get("TMPDIR", "")
    if tmpdir and Path(tmpdir).resolve().is_relative_to(tmp_dir().resolve()):
        return Path("C:/Windows/Temp") if sys.platform.startswith("win") else Path("/tmp")
    return Path(tempfile.gettempdir())


def _filesystem(path: Path) -> Optional[str]:
    try:
        lines = Path("/proc/mounts").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    best, fstype = "", None
    resolved = str(path.resolve())
    for line in lines:
        fields = line.split()
        if len(fields) < 3:
            continue
        mount = fields[1]
        inside = resolved == mount or resolved.startswith(mount.rstrip("/") + "/")
        if inside and len(mount) > len(best):
            best, fstype = mount, fields[2]
    return fstype


def measure(role: str, path: Path) -> Optional[Dict[str, object]]:
    """Size, free space and medium of the volume holding *path*; None when it does not exist."""
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    fstype = _filesystem(path)
    return {
        "role": role,
        "path": str(path),
        "total": usage.total,
        "free": usage.free,
        "fstype": fstype,
        "in_memory": fstype in {"tmpfs", "ramfs"} if fstype else None,
    }


def problems(volume: Dict[str, object]) -> List[str]:
    """What is wrong with *volume*; empty when it has room."""
    total, free = int(volume["total"]), int(volume["free"])
    found = []
    if free < MIN_FREE_BYTES:
        found.append(f"{free / GIB:.1f} GiB free")
    elif total and (total - free) / total > MAX_USED_FRACTION:
        found.append(f"{100 * (total - free) / total:.0f}% full")
    if volume["in_memory"] and total < MIN_MEMORY_TOTAL_BYTES:
        found.append(f"only {total / GIB:.1f} GiB, in memory")
    return found


def volumes() -> List[Dict[str, object]]:
    """The host spool and Gaia's tmp, each measured once even when both share a volume."""
    from gaia.paths import tmp_dir

    out = []
    for role, path in (("host spool", host_spool_dir()), ("Gaia tmp", tmp_dir())):
        measured = measure(role, path)
        if measured:
            out.append(measured)
    return out


def describe(volume: Dict[str, object]) -> str:
    """One line: role, path, size, free, medium."""
    medium = {True: "in memory", False: "on disk", None: "medium unknown"}[volume["in_memory"]]
    return (
        f"{volume['role']} {volume['path']}: {int(volume['total']) / GIB:.1f} GiB, "
        f"{int(volume['free']) / GIB:.1f} GiB free, {medium}"
    )


def health() -> Dict[str, str]:
    """``{"severity", "detail", "fix"}`` over every temp volume; warning when one is small or nearly full."""
    measured = volumes()
    if not measured:
        return {"severity": "info", "detail": "no temp folder could be measured", "fix": ""}
    warned = [v for v in measured if problems(v)]
    detail = "; ".join(
        describe(v) + (f" -- {', '.join(problems(v))}" if problems(v) else "") for v in measured
    )
    if not warned:
        return {"severity": "pass", "detail": detail, "fix": ""}
    return {"severity": "warning", "detail": detail, "fix": REMEDIES[platform_name()]}


def notice_line() -> str:
    """The one session-start line when a temp volume warns; "" otherwise."""
    lines = []
    for volume in volumes():
        found = problems(volume)
        if found:
            lines.append(f"{volume['role']} {volume['path']}: {', '.join(found)}")
    if not lines:
        return ""
    return f"Temp space low -- {'; '.join(lines)}. Remedy: {REMEDIES[platform_name()]}. `gaia doctor` shows the rest."
