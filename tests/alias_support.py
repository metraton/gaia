"""Declare classifier aliases for a test the way a user does: kind=tool rows with ``replaces``."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def declare_alias(monkeypatch, bin_dir: Path, *pairs: str) -> None:
    """Write one tool row per ``binary=cli`` pair, with the binary on PATH, and drop the alias cache."""
    from gaia.store.reader import user_tool_rows
    from gaia.store.writer import USER_WORKSPACE, upsert_memory
    from modules.security import cli_aliases

    monkeypatch.setenv("PATH", os.pathsep.join([os.environ.get("PATH", ""), str(bin_dir)]))
    for pair in pairs:
        binary, _, target = (part.strip() for part in pair.partition("="))
        if not shutil.which(binary):
            executable = bin_dir / binary
            executable.write_text("#!/bin/sh\n")
            executable.chmod(0o755)
        if any(row["binary"] == binary and row["replaces"] == (target or None) for row in user_tool_rows()):
            continue
        upsert_memory(USER_WORKSPACE, f"user_tool_{binary}", type="user", body="declared by the test",
                      description=f"run {binary} instead of {target}", kind="tool", binary=binary,
                      replaces=target or None)
    cli_aliases.reset_cache()
