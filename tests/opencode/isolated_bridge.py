"""Assert real filesystem resolution before executing the source bridge in-process.

Keeping the same process preserves the Bun parent's host-run attestation namespace.
No production resolver is patched and no repository state directory is inspected.
"""

import os
from pathlib import Path
import runpy
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "hooks"), str(ROOT)]


def main():
    """Fail closed before policy can access non-private HookState paths."""
    workspace = Path(os.environ["WORKSPACE"]).resolve()
    assert workspace != ROOT and not workspace.is_relative_to(ROOT)
    assert Path.cwd().resolve() == workspace
    private = workspace / ".claude"
    assert private.is_dir() and not private.is_symlink()
    from modules.core.paths import find_claude_dir, get_plugin_data_dir
    from modules.core.state import _get_state_dir, _get_state_file_path
    from gaia.paths import db_path, data_dir

    assert find_claude_dir().resolve() == private
    assert get_plugin_data_dir().resolve() == private
    assert _get_state_dir().resolve().parent == private
    assert _get_state_file_path().resolve().parent == private
    assert db_path().resolve() == Path(os.environ["GAIA_DB"]).resolve()
    assert data_dir().resolve() == Path(os.environ["GAIA_DATA_DIR"]).resolve()
    assert data_dir().resolve().is_relative_to(workspace)
    assert db_path().resolve().is_relative_to(workspace)
    assert Path(os.environ["GAIA_OPENCODE_ATTESTATION_DIR"]).resolve().is_relative_to(workspace)
    runpy.run_path(str(ROOT / "opencode" / "bridge.py"), run_name="__main__")


if __name__ == "__main__":
    main()
