#!/usr/bin/env python3
"""The .md carve-out of the protected set, measured on the live predicate.

Documentation under a hook tree is exempt because it cannot execute code; its
executable siblings are not. Both halves are asserted against
``protected_paths.is_protected_hook_path``, the predicate both write surfaces
consume.

Until this rewrite the file carried a COPY of an older, nested ``_is_protected``
and asserted against that, so it stayed green while the algorithm it claimed to
replicate was replaced -- and replaced precisely because anchoring the protected
set to the evaluating module's load path left every other hook tree ungated. The
``TestVerdictDoesNotFollowTheLoadPath`` class below is the assertion that copy
could not make.

Protection now follows the INSTALLATION, not the repository (decision
``decision_gaia_proteccion_sigue_a_la_instalacion_no_al_repo``): a Gaia source
checkout, HOOKS_DIR included, is DELIBERATELY ungated here -- an ordinary
project gated by git, not by this predicate. That is a different claim from the
load-path bug above: the bug ungated the checkout by accident, as a side
effect of where the evaluating module happened to load from; the current
design ungates it on purpose, regardless of load location, because it is a
checkout and not a live install.

Every call is written module-qualified so the binding resolves per call: a
predicate that breaks or disappears turns this file red instead of leaving it
measuring a symbol captured at import time.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security import protected_paths  # noqa: E402

# Hook trees this test did not load its code from: the harness install root and
# a package-store materialisation. Both are decided by path shape alone, so they
# hold on any machine and under any install mode.
HARNESS_HOOKS = Path(".claude") / "hooks"
PACKAGE_HOOKS = Path("/opt/store/node_modules/@jaguilar87/gaia/hooks")


class TestMdExemptionUnderHooks:
    """A .md under a hook tree is documentation and stays writable."""

    def test_md_at_the_hook_tree_root_is_not_protected(self):
        path = str(HOOKS_DIR / "README.md")
        assert protected_paths.is_protected_hook_path(path) is False

    def test_md_deeper_in_the_hook_tree_is_not_protected(self):
        path = str(HOOKS_DIR / "modules" / "README.md")
        assert protected_paths.is_protected_hook_path(path) is False


class TestSourceCheckoutExecutablesAreUngated:
    """HOOKS_DIR here is the SOURCE CHECKOUT tree (this repo's own hooks/),
    not an install. Protection follows the installation, not the repository
    (decision decision_gaia_proteccion_sigue_a_la_instalacion_no_al_repo): a
    checkout is an ordinary project gated by git, never by a per-file
    approval -- flipped from the prior expectation that these were protected.
    ``TestVerdictDoesNotFollowTheLoadPath`` below exercises the install shapes
    that DO stay protected."""

    def test_module_under_checkout_hooks_is_ungated(self):
        path = str(HOOKS_DIR / "modules" / "security" / "mutative_verbs.py")
        assert protected_paths.is_protected_hook_path(path) is False

    def test_session_module_under_checkout_hooks_is_ungated(self):
        path = str(HOOKS_DIR / "modules" / "session" / "pending_scanner.py")
        assert protected_paths.is_protected_hook_path(path) is False

    def test_adapter_under_checkout_hooks_is_ungated(self):
        path = str(HOOKS_DIR / "adapters" / "claude_code.py")
        assert protected_paths.is_protected_hook_path(path) is False


class TestNonHooksPathsUnchanged:
    """Outside every hook tree the predicate decides nothing."""

    def test_md_outside_a_hook_tree_is_not_protected(self):
        assert protected_paths.is_protected_hook_path("/tmp/foo.md") is False

    def test_code_outside_a_hook_tree_is_not_protected(self):
        assert protected_paths.is_protected_hook_path("/tmp/foo.py") is False


class TestVerdictDoesNotFollowTheLoadPath:
    """The assertion a load-path-anchored copy answers wrong on every case.

    These paths lie outside the hook tree this test imported its code from, so a
    predicate anchored on ``__file__`` reports them unprotected -- the regression
    that made the source checkout, the only place an edit is durable, the one
    tree left open.
    """

    def test_harness_hook_module_is_protected(self):
        path = str(HARNESS_HOOKS / "pre_tool_use.py")
        assert protected_paths.is_protected_hook_path(path) is True

    def test_package_store_hook_module_is_protected(self):
        path = str(PACKAGE_HOOKS / "modules" / "security" / "mutative_verbs.py")
        assert protected_paths.is_protected_hook_path(path) is True

    def test_the_carve_out_reaches_those_trees_too(self):
        assert protected_paths.is_protected_hook_path(
            str(HARNESS_HOOKS / "README.md")
        ) is False
