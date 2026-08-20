#!/usr/bin/env python3
"""Report the Write/Edit gate verdict for paths, under ONE hooks-tree load location.

Usage: python3 _protected_verdict_probe.py <hooks_dir> <repo_root> <path>...

Prints a single JSON object: {"hooks_dir": <resolved>, "verdicts": {path: verdict}}.

The hooks tree is loaded from ``sys.argv[1]``, so the module load location is an
ARGUMENT rather than a property of this file. That is the variable the invariance
oracle varies: a module tree can only be imported once per process under one
name, so each layout needs its own interpreter.

The verdict is read from the REAL PreToolUse entrypoint
(``ClaudeCodeAdapter._adapt_write_edit``) with the real parameter shape, never
from the internal predicate -- the entrypoint is what production calls, and the
defect lived in how the entrypoint derived its root. ``is_subagent=False`` is
used deliberately: the foreground branch answers with the native consent shape
and touches no approval store, so the probe measures the gate without minting
pending rows.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ASK_OR_DENY = ("ask", "deny")


def main() -> int:
    if len(sys.argv) < 4:
        print(json.dumps({"error": "usage: <hooks_dir> <repo_root> <path>..."}))
        return 2

    hooks_dir, repo_root = sys.argv[1], sys.argv[2]
    targets = sys.argv[3:]

    sys.path.insert(0, hooks_dir)
    sys.path.insert(0, repo_root)

    from adapters.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter()
    verdicts = {}
    for target in targets:
        response = adapter._adapt_write_edit(
            "Edit", {"file_path": target}, session_id="", is_subagent=False,
        )
        payload = response.output or {}
        specific = payload.get("hookSpecificOutput") or {}
        decision = specific.get("permissionDecision") or ""
        verdicts[target] = (
            "protected" if decision in _ASK_OR_DENY else "unprotected"
        )

    module = sys.modules["adapters.claude_code"]
    print(json.dumps({
        "hooks_dir": str(Path(module.__file__).parent.parent.resolve()),
        "verdicts": verdicts,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
