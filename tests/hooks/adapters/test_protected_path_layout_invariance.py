#!/usr/bin/env python3
"""Layout-invariance oracle for the Write/Edit protected-path gate.

Plan 63 order 13 (AC-10, AC-1). The defect being pinned is
CONFIGURATION-dependent: the protected root was the parent of the directory the
running hook module was loaded from, so the very same path was protected before
a dev install and unprotected after it with no code change. A point assertion
("this path is protected") therefore proves nothing at all -- it passes in the
layout that happens to be installed and says nothing about the other.

What must hold is INVARIANCE: the verdict SET of the real PreToolUse entrypoint
is identical under physically different module load locations. Each layout runs
in its own interpreter via ``_protected_verdict_probe.py``, because one module
tree cannot be imported twice in one process under the same name.

The fail-closed layout is the negative that fails if the implementation reads
the load location at all: with the identity source made unresolvable, the
structural lane must still fire and the protected set must not shrink.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
IN_PLACE_HOOKS = REPO_ROOT / "hooks"
PROBE = Path(__file__).parent / "_protected_verdict_probe.py"

# The harness install tree, addressed the way a caller addresses it (the literal
# `.claude/hooks` path) AND the way the filesystem resolves it (realpath, which
# lands in the package store when the install materialised there). realpath is
# unconditional -- it returns the literal unchanged when nothing is a symlink --
# so neither target can turn into a skipped case.
HARNESS_HOOKS_LITERAL = Path.home() / ".claude" / "hooks"
HARNESS_HOOKS_REAL = Path(os.path.realpath(HARNESS_HOOKS_LITERAL))

# The workspace-level install beside the checkout. On this machine the two real
# layouts differ: the user-level one is a symlink back into the checkout, the
# workspace-level one is materialised into a package store. Both are addressed.
WORKSPACE_HOOKS_LITERAL = REPO_ROOT.parent / ".claude" / "hooks"
WORKSPACE_HOOKS_REAL = Path(os.path.realpath(WORKSPACE_HOOKS_LITERAL))

# A package-store shape with no filesystem dependency, so the store layout is
# exercised even on a machine where the local install is a symlink-back.
SYNTHETIC_STORE_HOOKS = Path(
    "/tmp/gaia-store-probe/node_modules/@jaguilar87/gaia/hooks"
)

TARGETS = {
    "source_checkout_py": str(
        IN_PLACE_HOOKS / "modules" / "security" / "mutative_verbs.py"
    ),
    "source_checkout_adapter": str(IN_PLACE_HOOKS / "adapters" / "claude_code.py"),
    "source_checkout_md": str(IN_PLACE_HOOKS / "README.md"),
    "harness_literal_py": str(HARNESS_HOOKS_LITERAL / "pre_tool_use.py"),
    "harness_literal_md": str(HARNESS_HOOKS_LITERAL / "README.md"),
    "harness_real_py": str(HARNESS_HOOKS_REAL / "pre_tool_use.py"),
    "workspace_literal_py": str(WORKSPACE_HOOKS_LITERAL / "pre_tool_use.py"),
    "workspace_real_py": str(WORKSPACE_HOOKS_REAL / "pre_tool_use.py"),
    "store_shape_py": str(SYNTHETIC_STORE_HOOKS / "modules" / "security" / "tiers.py"),
    "settings": str(Path.home() / ".claude" / "settings.json"),
    "settings_local": str(Path.home() / ".claude" / "settings.local.json"),
    "outside_py": "/tmp/gaia-invariance-probe/unrelated.py",
    "outside_md": "/tmp/gaia-invariance-probe/unrelated.md",
}

EXPECTED = {
    "source_checkout_py": "protected",
    "source_checkout_adapter": "protected",
    "source_checkout_md": "unprotected",
    "harness_literal_py": "protected",
    "harness_literal_md": "unprotected",
    "harness_real_py": "protected",
    "workspace_literal_py": "protected",
    "workspace_real_py": "protected",
    "store_shape_py": "protected",
    "settings": "protected",
    "settings_local": "protected",
    "outside_py": "unprotected",
    "outside_md": "unprotected",
}


def _run_probe(hooks_dir: Path, env_overrides: dict | None = None) -> dict:
    """Return {label: verdict} for TARGETS, with the tree loaded from hooks_dir."""
    assert PROBE.is_file(), f"probe missing: {PROBE}"
    assert (hooks_dir / "adapters" / "claude_code.py").is_file(), (
        f"subject missing under {hooks_dir} -- an absent subject is the same "
        f"defect as a green test over a shape production never emits"
    )

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if env_overrides:
        env.update(env_overrides)

    labels = list(TARGETS)
    completed = subprocess.run(
        [sys.executable, str(PROBE), str(hooks_dir), str(REPO_ROOT)]
        + [TARGETS[label] for label in labels],
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, (
        f"probe failed rc={completed.returncode}\n"
        f"stdout={completed.stdout}\nstderr={completed.stderr}"
    )
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    return {label: payload["verdicts"][TARGETS[label]] for label in labels}


@pytest.fixture(scope="module")
def copied_hooks(tmp_path_factory) -> Path:
    """A physically different load location: the tree copied elsewhere."""
    destination = tmp_path_factory.mktemp("relocated") / "hooks"
    shutil.copytree(
        IN_PLACE_HOOKS,
        destination,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return destination


@pytest.fixture(scope="module")
def verdicts_in_place() -> dict:
    return _run_probe(IN_PLACE_HOOKS)


@pytest.fixture(scope="module")
def verdicts_relocated(copied_hooks) -> dict:
    return _run_probe(copied_hooks)


@pytest.fixture(scope="module")
def verdicts_identity_unresolvable(tmp_path_factory, copied_hooks) -> dict:
    """Both layouts with the declared-identity source pointed at nothing."""
    void = tmp_path_factory.mktemp("no-identity")
    overrides = {
        "GAIA_DB": str(void / "absent.db"),
        "GAIA_DATA_DIR": str(void / "absent-root"),
    }
    return {
        "in_place": _run_probe(IN_PLACE_HOOKS, overrides),
        "relocated": _run_probe(copied_hooks, overrides),
    }


class TestLayoutInvariance:
    def test_verdict_sets_are_identical_across_load_locations(
        self, verdicts_in_place, verdicts_relocated
    ):
        assert verdicts_in_place == verdicts_relocated, (
            "the gate's verdict set moved when the module tree moved -- the "
            "scope of a security control is still a function of the deployment"
        )

    def test_in_place_layout_matches_the_required_verdicts(self, verdicts_in_place):
        assert verdicts_in_place == EXPECTED

    def test_relocated_layout_matches_the_required_verdicts(self, verdicts_relocated):
        assert verdicts_relocated == EXPECTED


class TestNoTrade:
    """The installed-copy protection must survive; this is a union, not a move."""

    @pytest.mark.parametrize(
        "label",
        [
            "harness_literal_py",
            "harness_real_py",
            "workspace_literal_py",
            "workspace_real_py",
            "store_shape_py",
        ],
    )
    def test_harness_install_tree_still_gated_in_place(
        self, verdicts_in_place, label
    ):
        assert verdicts_in_place[label] == "protected"

    @pytest.mark.parametrize(
        "label",
        [
            "harness_literal_py",
            "harness_real_py",
            "workspace_literal_py",
            "workspace_real_py",
            "store_shape_py",
        ],
    )
    def test_harness_install_tree_still_gated_relocated(
        self, verdicts_relocated, label
    ):
        assert verdicts_relocated[label] == "protected"


class TestSourceCheckoutGated:
    @pytest.mark.parametrize(
        "label", ["source_checkout_py", "source_checkout_adapter"]
    )
    def test_source_checkout_gated_in_place(self, verdicts_in_place, label):
        assert verdicts_in_place[label] == "protected"

    @pytest.mark.parametrize(
        "label", ["source_checkout_py", "source_checkout_adapter"]
    )
    def test_source_checkout_gated_relocated(self, verdicts_relocated, label):
        assert verdicts_relocated[label] == "protected"


class TestPreservedCarveOuts:
    @pytest.mark.parametrize("label", ["source_checkout_md", "harness_literal_md"])
    def test_md_exempt_in_both_trees(
        self, verdicts_in_place, verdicts_relocated, label
    ):
        assert verdicts_in_place[label] == "unprotected"
        assert verdicts_relocated[label] == "unprotected"

    @pytest.mark.parametrize("label", ["settings", "settings_local"])
    def test_settings_special_case_unchanged(
        self, verdicts_in_place, verdicts_relocated, label
    ):
        assert verdicts_in_place[label] == "protected"
        assert verdicts_relocated[label] == "protected"

    @pytest.mark.parametrize("label", ["outside_py", "outside_md"])
    def test_paths_outside_both_trees_are_not_protected(
        self, verdicts_in_place, verdicts_relocated, label
    ):
        assert verdicts_in_place[label] == "unprotected"
        assert verdicts_relocated[label] == "unprotected"


class TestFailsClosed:
    def test_protected_set_does_not_shrink_without_identity(
        self, verdicts_identity_unresolvable, verdicts_in_place
    ):
        for layout, verdicts in verdicts_identity_unresolvable.items():
            shrunk = [
                label
                for label, verdict in verdicts.items()
                if verdict == "unprotected" and verdicts_in_place[label] == "protected"
            ]
            assert not shrunk, (
                f"{layout}: unresolvable identity shrank the protected set "
                f"for {shrunk} -- resolution must fail CLOSED"
            )

    @pytest.mark.parametrize(
        "label",
        [
            "harness_literal_py",
            "harness_real_py",
            "workspace_literal_py",
            "workspace_real_py",
            "store_shape_py",
            "source_checkout_py",
        ],
    )
    def test_structural_lane_still_fires_without_identity(
        self, verdicts_identity_unresolvable, label
    ):
        for layout, verdicts in verdicts_identity_unresolvable.items():
            assert verdicts[label] == "protected", f"{layout}/{label}"

    def test_predicate_never_reads_its_own_load_location(self):
        """The negative read directly off the syntax tree, not off the prose.

        A substring search would match the docstring that DESCRIBES the defect,
        so the check is for a ``__file__`` reference the interpreter would
        evaluate.
        """
        module = REPO_ROOT / "hooks" / "modules" / "security" / "protected_paths.py"
        assert module.is_file(), f"shared predicate missing: {module}"
        tree = ast.parse(module.read_text(encoding="utf-8"))
        references = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "__file__"
        ]
        assert not references, (
            "the shared predicate reads the load location of the evaluating "
            "module -- that is the defect, not the fix"
        )
