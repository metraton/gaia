"""Production plugin-to-bridge coverage for protected file-tool normalization."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "tests" / "opencode" / "protected_edit_driver.ts"


def test_driver_cannot_replace_or_wrap_the_production_bridge():
    driver = DRIVER.read_text()
    plugin = (ROOT / "opencode" / "plugin.ts").read_text()

    assert "gaiaBridge" not in driver
    assert "protected_edit_bridge" not in driver
    assert 'import { GaiaOpenCodePlugin } from "../../opencode/plugin.ts"' in driver
    assert "GaiaOpenCodePlugin({\n  client," in driver
    assert "export const GaiaOpenCodePlugin = async" in plugin
    assert "export default" in plugin
    assert 'Bun.spawn(["python3", bridgePath]' in plugin
    assert not (ROOT / "tests" / "opencode" / "protected_edit_bridge.py").exists()


@pytest.fixture(autouse=True)
def isolated_gaia_db(tmp_path, monkeypatch, bootstrapped_db_template):
    from tests.conftest import copy_bootstrapped_db

    db_path = tmp_path / "protected-edit.db"
    copy_bootstrapped_db(bootstrapped_db_template, db_path)
    monkeypatch.setenv("GAIA_DB", str(db_path))
    return db_path


def _patch(key: str, target: str) -> dict[str, str]:
    return {
        key: "\n".join([
            "*** Begin Patch", f"*** Update File: {target}",
            "@@", "-ORIGINAL", "+CHANGED", "*** End Patch",
        ])
    }


def _drive(directory: Path | None, worktree: Path | None, steps: list[dict]):
    scenario = {
        "directory": str(directory) if directory is not None else None,
        "worktree": str(worktree) if worktree is not None else None,
        "steps": steps,
    }
    env = os.environ.copy()
    env["GAIA_DEBUG"] = "1"
    result = subprocess.run(
        ["bun", str(DRIVER), json.dumps(scenario)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=300,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    driven = json.loads(result.stdout.strip().splitlines()[-1])
    traces = []
    pattern = re.compile(r"^\[gaia-opencode-bridge:(request|response)\] (.+)$")
    for line in result.stderr.splitlines():
        matched = pattern.match(line)
        if matched:
            traces.append({"stage": matched.group(1), "payload": json.loads(matched.group(2))})
    driven["bridgeTraces"] = traces
    return driven


def _exchange(driven: dict, call_id: str) -> dict:
    traces = driven["bridgeTraces"]
    for index, trace in enumerate(traces):
        payload = trace["payload"]
        if trace["stage"] == "request" and payload.get("callID") == call_id:
            response = traces[index + 1]
            assert response["stage"] == "response"
            return {"sent": payload, "received": response["payload"]}
    raise AssertionError(f"no production bridge request for call {call_id}")


def _has_request(driven: dict, call_id: str) -> bool:
    return any(
        trace["stage"] == "request" and trace["payload"].get("callID") == call_id
        for trace in driven["bridgeTraces"]
    )


def _step(label: str, tool: str, args: object) -> dict:
    return {"label": label, "tool": tool, "args": args}


def _workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "workspace"
    hooks = root / "hooks"
    hooks.mkdir(parents=True)
    protected = hooks / "guard.py"
    protected.write_text("ORIGINAL\n")
    (root / "package.json").write_text(json.dumps({"name": "@jaguilar87/gaia"}))
    unprotected = root / "src" / "safe.py"
    unprotected.parent.mkdir()
    unprotected.write_text("SAFE\n")
    return root, protected, unprotected


def test_exhaustive_file_alias_payload_and_path_matrix_reaches_real_bridge(
    tmp_path, isolated_gaia_db,
):
    root, protected, unprotected = _workspace(tmp_path)
    nested = root / "nested"
    nested.mkdir()
    symlink = nested / "hook-link"
    symlink.symlink_to(root / "hooks", target_is_directory=True)

    edit_aliases = ["Edit", "edit", "EDIT", "e_d-i.t"]
    write_aliases = ["Write", "write", "WRITE", "w-r_i.t e"]
    patch_aliases = [
        "ApplyPatch", "apply_patch", "APPLY-PATCH", "apply.patch",
        "apply patch", "applyPatch",
    ]
    path_keys = ["path", "file_path", "filePath", "file-path"]
    patch_keys = ["patchText", "patch_text", "patch-text", "patch"]
    targets = [
        os.path.relpath(protected, nested),
        str(protected),
        "hook-link/guard.py",
        "hook-link/../hooks/guard.py",
    ]

    cases = []
    expected = {}
    for alias in edit_aliases + write_aliases:
        for key in path_keys:
            for target in targets:
                label = f"{alias}|{key}|{target}"
                cases.append(_step(label, alias, {key: target, "content": "CHANGED"}))
                expected[label] = protected.resolve()
    for alias in patch_aliases:
        for key in patch_keys:
            for target in targets:
                label = f"{alias}|{key}|{target}"
                cases.append(_step(label, alias, _patch(key, target)))
                expected[label] = protected.resolve()

    cases.extend([
        _step("edit-unprotected", "Edit", {"path": str(unprotected), "content": "ok"}),
        _step("write-unprotected", "Write", {"filePath": "../src/safe.py", "content": "ok"}),
        _step("patch-unprotected", "ApplyPatch", _patch("patch_text", "../src/safe.py")),
    ])
    before = protected.read_bytes()
    driven = _drive(nested, root, cases)
    assert len(driven["results"]) == len(cases) > 100
    assert protected.read_bytes() == before

    by_label = {result["label"]: result for result in driven["results"]}
    approval_ids = set()
    for label, target in expected.items():
        result = by_label[label]
        assert result["allowed"] is False, result
        assert len(result["permissionIndexes"]) == 1, result
        exchange = _exchange(driven, result["callID"])
        assert exchange["received"]["action"] == "deny", exchange
        approval_id = exchange["received"].get("approval_id")
        assert re.fullmatch(r"P-[0-9a-f]{32}", approval_id or ""), exchange
        approval_ids.add(approval_id)
        permission = driven["permissionAsks"][result["permissionIndexes"][0]]["permission"]
        assert permission["metadata"]["gaiaApprovalID"] == approval_id
        assert exchange["sent"]["cwd"] == str(nested.resolve())
        assert exchange["sent"]["worktree"] == str(root.resolve())
        assert exchange["sent"]["originalTool"] == label.split("|", 1)[0]
        if exchange["sent"]["tool"] == "apply_patch":
            assert exchange["sent"]["args"]["file_paths"] == [str(target)]
            assert f"*** Update File: {target}" in exchange["sent"]["args"]["patchText"]
        else:
            assert exchange["sent"]["tool"] in {"Edit", "Write"}
            assert exchange["sent"]["args"]["file_path"] == str(target)

    for label in ("edit-unprotected", "write-unprotected", "patch-unprotected"):
        result = by_label[label]
        assert result["allowed"] is True, result
        assert result["permissionIndexes"] == []
        exchange = _exchange(driven, result["callID"])
        assert exchange["received"]["action"] == "allow", exchange

    with sqlite3.connect(isolated_gaia_db) as connection:
        stored_ids = {
            row[0] for row in connection.execute(
                "SELECT id FROM approvals WHERE id LIKE 'P-%'"
            )
        }
    assert approval_ids <= stored_ids
    sample_result = by_label[next(iter(expected))]
    sample = _exchange(driven, sample_result["callID"])
    assert sample["sent"]["roleContext"] == {
        "role": "gaia-system",
        "issuer": "opencode-runtime",
        "verified": True,
        "attestation_present": True,
    }
    print("OPENCODE_PLUGIN_BRIDGE_SAMPLE " + json.dumps(sample, sort_keys=True))
    print(f"OPENCODE_PROTECTED_EDIT_MATRIX cases={len(cases)} skips=0")


def test_literal_apply_patch_relative_target_reaches_guard_before_native_patch(
    tmp_path, isolated_gaia_db,
):
    root, protected, _ = _workspace(tmp_path)
    patch = _patch("patchText", "hooks/guard.py")

    driven = _drive(root, root, [
        _step("native-identity", "apply_patch", patch),
    ])

    result = driven["results"][0]
    assert result["allowed"] is False
    assert result["permissionIndexes"] == [0]
    exchange = _exchange(driven, result["callID"])
    assert exchange["sent"]["tool"] == "apply_patch"
    assert exchange["sent"]["args"]["file_paths"] == [str(protected.resolve())]
    assert exchange["received"]["action"] == "deny"
    assert re.fullmatch(r"P-[0-9a-f]{32}", exchange["received"].get("approval_id", ""))
    assert protected.read_text() == "ORIGINAL\n"


def test_multiple_patch_paths_preserve_order_and_any_invalid_target_fails_closed(tmp_path):
    root, protected, _ = _workspace(tmp_path)
    second = root / "hooks" / "second.py"
    second.write_text("SECOND\n")
    valid_patch = "\n".join([
        "*** Begin Patch",
        "*** Add File: hooks/new.py",
        "+NEW",
        "*** Update File: hooks/guard.py",
        "*** Move to: hooks/moved.py",
        "@@",
        "-ORIGINAL",
        "+MOVED",
        "*** Update File: hooks/guard.py",
        "@@",
        "-MOVED",
        "+MOVED-AGAIN",
        "*** Delete File: hooks/second.py",
        "*** End Patch",
    ])
    invalid_patch = valid_patch.replace("hooks/second.py", "/")
    driven = _drive(root, root, [
        _step("ordered", "ApplyPatch", {"patchText": valid_patch}),
        _step("one-invalid", "apply_patch", {"patchText": invalid_patch}),
        _step(
            "conflicting-path-keys",
            "Edit",
            {"path": str(protected), "file_path": str(second), "content": "no"},
        ),
        _step(
            "malformed-body",
            "ApplyPatch",
            {"patchText": "*** Begin Patch\n*** Update File: hooks/guard.py\n*** End Patch"},
        ),
    ])

    ordered = driven["results"][0]
    assert ordered["allowed"] is False
    exchange = _exchange(driven, ordered["callID"])
    assert exchange["sent"]["args"]["file_paths"] == [
        str((root / "hooks" / "new.py").resolve()),
        str(protected.resolve()),
        str((root / "hooks" / "moved.py").resolve()),
        str(protected.resolve()),
        str(second.resolve()),
    ]
    for result in driven["results"][1:]:
        assert result["allowed"] is False
        assert result["permissionIndexes"] == []
        assert not _has_request(driven, result["callID"])
        assert "Gaia denied file tool" in result["error"]


def test_missing_or_ambiguous_workspace_context_denies_before_bridge(tmp_path):
    root, protected, _ = _workspace(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    cases = [
        ("missing", None, None),
        ("ambiguous", root, other),
    ]
    for label, directory, worktree in cases:
        driven = _drive(
            directory,
            worktree,
            [_step(label, "Write", {"path": str(protected), "content": "no"})],
        )
        result = driven["results"][0]
        assert result["allowed"] is False
        assert result["permissionIndexes"] == []
        assert not _has_request(driven, result["callID"])
        assert "Gaia denied file tool" in result["error"]


def test_unresolvable_targets_deny_before_bridge(tmp_path):
    root, _, _ = _workspace(tmp_path)
    dangling = root / "dangling"
    dangling.symlink_to(root / "missing-target", target_is_directory=True)
    driven = _drive(
        root,
        root,
        [
            _step("dangling", "Edit", {"path": "dangling/file.py", "content": "no"}),
            _step(
                "missing-traversal", "Edit",
                {"path": "missing/../hooks/guard.py", "content": "no"},
            ),
        ],
    )

    for result in driven["results"]:
        assert result["allowed"] is False
        assert result["permissionIndexes"] == []
        assert not _has_request(driven, result["callID"])
        assert "Gaia denied file tool" in result["error"]


def test_invalid_args_and_patch_payloads_fail_before_the_production_bridge(tmp_path):
    root, protected, _ = _workspace(tmp_path)
    valid_patch = _patch("patchText", "hooks/guard.py")["patchText"]
    invalid_cases = [
        _step("args-null", "Edit", None),
        _step("args-array", "Write", []),
        _step("args-string", "Edit", "hooks/guard.py"),
        _step("args-number", "Write", 7),
        _step("args-boolean", "Edit", False),
        _step("path-absent", "Edit", {"content": "x"}),
        _step("path-null", "Write", {"path": None, "content": "x"}),
        _step("path-empty", "Edit", {"file_path": "", "content": "x"}),
        _step("path-whitespace", "Write", {"path": "  ", "content": "x"}),
        _step("path-number", "Edit", {"path": 7, "content": "x"}),
        _step("path-array", "Write", {"path": ["hooks/guard.py"], "content": "x"}),
        _step("path-object", "Edit", {"path": {"value": "hooks/guard.py"}, "content": "x"}),
        _step("path-nul", "Write", {"path": "hooks/guard.py\0tail", "content": "x"}),
        _step("path-root", "Edit", {"path": "/", "content": "x"}),
        _step(
            "path-alias-conflict", "Write",
            {"path": str(protected), "filePath": "src/other.py", "content": "x"},
        ),
        _step("patch-absent", "ApplyPatch", {}),
        _step("patch-null", "apply_patch", {"patchText": None}),
        _step("patch-number", "ApplyPatch", {"patch": 9}),
        _step("patch-empty", "apply_patch", {"patchText": ""}),
        _step("patch-no-envelope", "ApplyPatch", {"patchText": "*** Update File: hooks/guard.py"}),
        _step(
            "patch-missing-end", "apply_patch",
            {"patchText": "*** Begin Patch\n*** Delete File: hooks/guard.py"},
        ),
        _step("patch-no-operation", "ApplyPatch", {"patchText": "*** Begin Patch\n*** End Patch"}),
        _step(
            "patch-unsupported-marker", "apply_patch",
            {"patchText": "*** Begin Patch\n*** Rename File: hooks/guard.py\n*** End Patch"},
        ),
        _step(
            "patch-update-no-body", "ApplyPatch",
            {"patchText": "*** Begin Patch\n*** Update File: hooks/guard.py\n*** End Patch"},
        ),
        _step(
            "patch-update-bad-body", "apply_patch",
            {"patchText": "*** Begin Patch\n*** Update File: hooks/guard.py\ninvalid\n*** End Patch"},
        ),
        _step(
            "patch-add-bad-body", "ApplyPatch",
            {"patchText": "*** Begin Patch\n*** Add File: hooks/new.py\nplain\n*** End Patch"},
        ),
        _step(
            "patch-move-without-update", "apply_patch",
            {"patchText": "*** Begin Patch\n*** Move to: hooks/new.py\n*** End Patch"},
        ),
        _step(
            "patch-move-after-body", "ApplyPatch",
            {"patchText": "*** Begin Patch\n*** Update File: hooks/guard.py\n@@\n-X\n+Y\n*** Move to: hooks/new.py\n*** End Patch"},
        ),
        _step(
            "patch-content-outside-operation", "apply_patch",
            {"patchText": "*** Begin Patch\n+orphan\n*** End Patch"},
        ),
        _step(
            "patch-delete-with-content", "ApplyPatch",
            {"patchText": "*** Begin Patch\n*** Delete File: hooks/guard.py\n-extra\n*** End Patch"},
        ),
        _step(
            "patch-root-target", "apply_patch",
            {"patchText": "*** Begin Patch\n*** Delete File: /\n*** End Patch"},
        ),
        _step(
            "patch-nul-target", "ApplyPatch",
            {"patchText": "*** Begin Patch\n*** Delete File: hooks/guard.py\0tail\n*** End Patch"},
        ),
        _step(
            "patch-alias-conflict", "apply_patch",
            {"patchText": valid_patch, "patch": valid_patch.replace("guard.py", "other.py")},
        ),
    ]

    before = protected.read_bytes()
    driven = _drive(root, root, invalid_cases)
    assert len(driven["results"]) == len(invalid_cases) >= 30
    assert protected.read_bytes() == before
    for result in driven["results"]:
        assert result["allowed"] is False, result
        assert result["permissionIndexes"] == [], result
        assert not _has_request(driven, result["callID"]), result
        assert "Gaia denied file tool" in result["error"], result
    print(f"OPENCODE_INVALID_PAYLOAD_MATRIX cases={len(invalid_cases)} skips=0")
