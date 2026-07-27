"""Tests for gaia.dev_builds -- the per-machine dev-iteration counter.

Each test names the property it pins rather than the line it covers. The four
properties: a repack with identical hooks content does not advance the counter;
a changed hooks tree advances it by exactly one; a base-version change starts
its own sequence; and every reader degrades to the bare version rather than
failing when the sidecar is absent, corrupt, or unreadable.

Where a test has a precondition (e.g. "both runs really did record the same
digest"), the precondition is measured by reading the sidecar's raw JSON while
the assertion goes through the public API -- two independent channels, so a
sentinel cannot pass by tautology.
"""

import json
import os
import stat
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia import dev_builds


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point GAIA_DATA_DIR at an isolated tree so the real ~/.gaia is never touched."""
    root = tmp_path / "gaia-data"
    root.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(root))
    return root


def _raw_sidecar(path: Path) -> dict:
    """Read the sidecar's raw JSON -- the channel independent of the module API."""
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Sidecar location
# ---------------------------------------------------------------------------

class TestSidecarPath:
    def test_lives_under_state_dir_and_follows_the_data_dir_override(self, data_dir):
        assert dev_builds.sidecar_path() == data_dir / "state" / "dev-builds.json"

    def test_path_is_recomputed_per_call_not_cached(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "a"))
        first = dev_builds.sidecar_path()
        monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "b"))
        assert dev_builds.sidecar_path() != first


# ---------------------------------------------------------------------------
# Property 1: identical hooks content must not advance the counter
# ---------------------------------------------------------------------------

class TestUnchangedContentDoesNotAdvance:
    def test_two_runs_with_the_same_digest_leave_the_count_at_one(self, data_dir):
        dev_builds.record_build("5.3.0", "fb27693c")
        dev_builds.record_build("5.3.0", "fb27693c")

        # Precondition, measured off the raw file: both runs concerned the SAME
        # digest, so this is genuinely the no-change case and not two different
        # builds that happened to collide on a count.
        stored = _raw_sidecar(dev_builds.sidecar_path())["builds"]["5.3.0"]
        assert stored["hooks_hash"] == "fb27693c"

        # Assertion, through the public API.
        assert dev_builds.read_record("5.3.0")["count"] == 1

    def test_ten_identical_repacks_still_read_as_one_iteration(self, data_dir):
        for _ in range(10):
            dev_builds.record_build("5.3.0", "same")
        assert dev_builds.read_record("5.3.0")["count"] == 1

    def test_unchanged_run_does_not_rewrite_the_sidecar(self, data_dir):
        dev_builds.record_build("5.3.0", "fb27693c")
        path = dev_builds.sidecar_path()
        before = path.read_bytes()

        dev_builds.record_build("5.3.0", "fb27693c")

        # No write at all is a stronger guarantee than an equal count: it means
        # the no-change path cannot corrupt or churn the file either.
        assert path.read_bytes() == before

    def test_unchanged_run_returns_the_record_in_effect(self, data_dir):
        dev_builds.record_build("5.3.0", "fb27693c")
        again = dev_builds.record_build("5.3.0", "fb27693c")
        assert again["count"] == 1
        assert again["hooks_hash"] == "fb27693c"


# ---------------------------------------------------------------------------
# Property 2: a changed hooks tree advances the counter by exactly one
# ---------------------------------------------------------------------------

class TestChangedContentAdvancesByOne:
    def test_first_build_of_a_version_starts_at_one(self, data_dir):
        assert dev_builds.record_build("5.3.0", "aaaaaaaa")["count"] == 1

    def test_changed_digest_increments_by_exactly_one(self, data_dir):
        dev_builds.record_build("5.3.0", "aaaaaaaa")
        record = dev_builds.record_build("5.3.0", "bbbbbbbb")

        assert record["count"] == 2
        # The recorded identity moved to the new build, so a THIRD run with the
        # new digest is the no-change case rather than another increment.
        assert record["hooks_hash"] == "bbbbbbbb"
        assert dev_builds.record_build("5.3.0", "bbbbbbbb")["count"] == 2

    def test_alternating_digests_count_every_change(self, data_dir):
        for digest in ("a", "b", "a", "b"):
            dev_builds.record_build("5.3.0", digest)
        assert dev_builds.read_record("5.3.0")["count"] == 4

    def test_interleaved_identical_repacks_do_not_inflate_the_count(self, data_dir):
        # The real dev loop: edit, pack, pack, pack, edit, pack.
        for digest in ("a", "a", "a", "b", "b"):
            dev_builds.record_build("5.3.0", digest)
        assert dev_builds.read_record("5.3.0")["count"] == 2

    def test_empty_digest_is_never_stored_as_an_identity(self, data_dir):
        # hooks_content_hash returns "" for a tree it could not digest; that is
        # an absence, not a new build.
        assert dev_builds.record_build("5.3.0", "") is None
        assert dev_builds.read_record("5.3.0") is None

    def test_empty_digest_after_a_real_build_leaves_the_record_intact(self, data_dir):
        dev_builds.record_build("5.3.0", "aaaaaaaa")
        assert dev_builds.record_build("5.3.0", "")["count"] == 1
        assert dev_builds.read_record("5.3.0")["hooks_hash"] == "aaaaaaaa"


# ---------------------------------------------------------------------------
# Property 3: a base-version change starts its own sequence
# ---------------------------------------------------------------------------

class TestPerVersionKeying:
    def test_new_base_version_starts_at_one_regardless_of_history(self, data_dir):
        for digest in ("a", "b", "c", "d", "e"):
            dev_builds.record_build("5.3.0", digest)
        assert dev_builds.read_record("5.3.0")["count"] == 5

        assert dev_builds.record_build("5.4.0", "f")["count"] == 1

    def test_counts_are_independent_per_version(self, data_dir):
        dev_builds.record_build("5.3.0", "a")
        dev_builds.record_build("5.4.0", "b")
        dev_builds.record_build("5.4.0", "c")

        assert dev_builds.read_record("5.3.0")["count"] == 1
        assert dev_builds.read_record("5.4.0")["count"] == 2

        # Both survive in one sidecar -- a per-version key, not a rewritten file.
        assert set(_raw_sidecar(dev_builds.sidecar_path())["builds"]) == {"5.3.0", "5.4.0"}

    def test_returning_to_an_older_version_resumes_its_own_count(self, data_dir):
        dev_builds.record_build("5.3.0", "a")
        dev_builds.record_build("5.4.0", "b")
        assert dev_builds.record_build("5.3.0", "z")["count"] == 2

    def test_same_digest_under_a_different_version_still_counts_as_that_version_s_first(self, data_dir):
        dev_builds.record_build("5.3.0", "shared")
        assert dev_builds.record_build("5.4.0", "shared")["count"] == 1

    def test_empty_version_is_not_a_key(self, data_dir):
        assert dev_builds.record_build("", "a") is None
        assert dev_builds.read_record("") is None
        assert not dev_builds.sidecar_path().exists()


# ---------------------------------------------------------------------------
# Property 4: every reader degrades to the bare version, nothing raises
# ---------------------------------------------------------------------------

class TestDegradesInsteadOfFailing:
    def test_absent_sidecar_reads_as_no_record(self, data_dir):
        assert dev_builds.read_record("5.3.0") is None
        assert dev_builds.describe_version("5.3.0") == "5.3.0"

    @pytest.mark.parametrize(
        "body",
        [
            "",
            "not json at all",
            "{",
            "[]",
            "null",
            '{"builds": {}}',
            '{"version": 999, "builds": {"5.3.0": {"count": 4}}}',
            '{"version": 1, "builds": []}',
            '{"version": 1, "builds": {"5.3.0": "nope"}}',
            '{"version": 1, "builds": {"5.3.0": {"count": "seven"}}}',
            '{"version": 1, "builds": {"5.3.0": {"count": 0}}}',
            '{"version": 1, "builds": {"5.3.0": {"count": -3}}}',
            '{"version": 1, "builds": {"5.3.0": {"count": true}}}',
        ],
        ids=[
            "empty", "garbage", "truncated", "array", "null", "no-schema-version",
            "unknown-schema-version", "builds-not-a-dict", "record-not-a-dict",
            "count-not-an-int", "count-zero", "count-negative", "count-is-bool",
        ],
    )
    def test_corrupt_sidecar_degrades_to_the_bare_version(self, data_dir, body):
        path = dev_builds.sidecar_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

        assert dev_builds.read_record("5.3.0") is None
        assert dev_builds.describe_version("5.3.0") == "5.3.0"

    def test_corrupt_sidecar_is_recoverable_by_the_next_real_build(self, data_dir):
        path = dev_builds.sidecar_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("}{ corrupt", encoding="utf-8")

        assert dev_builds.record_build("5.3.0", "aaaaaaaa")["count"] == 1
        assert dev_builds.describe_version("5.3.0") == "5.3.0 (dev.1, build aaaaaaaa)"

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the read-only bit")
    def test_unreadable_sidecar_degrades_instead_of_raising(self, data_dir):
        path = dev_builds.sidecar_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"version": 1, "builds": {"5.3.0": {"count": 3}}}', encoding="utf-8")
        path.chmod(0o000)
        try:
            assert dev_builds.read_record("5.3.0") is None
            assert dev_builds.describe_version("5.3.0") == "5.3.0"
        finally:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the read-only bit")
    def test_unwritable_state_dir_does_not_fail_record_build(self, data_dir):
        state = data_dir / "state"
        state.mkdir(parents=True, exist_ok=True)
        state.chmod(0o500)
        try:
            assert dev_builds.record_build("5.3.0", "aaaaaaaa") is None
            assert dev_builds.describe_version("5.3.0") == "5.3.0"
        finally:
            state.chmod(0o700)

    def test_describe_version_survives_a_raising_state_dir(self, data_dir, monkeypatch):
        import gaia.paths.resolver as resolver

        def _boom():
            raise RuntimeError("simulated resolver failure")

        monkeypatch.setattr(resolver, "state_dir", _boom)
        monkeypatch.setattr("gaia.paths.state_dir", _boom)

        assert dev_builds.describe_version("5.3.0") == "5.3.0"
        assert dev_builds.read_record("5.3.0") is None
        assert dev_builds.record_build("5.3.0", "aaaaaaaa") is None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

class TestFormatLabel:
    def test_renders_count_and_digest(self):
        record = {"count": 7, "hooks_hash": "fb27693c"}
        assert dev_builds.format_label("5.3.0", record) == "5.3.0 (dev.7, build fb27693c)"

    def test_no_record_renders_the_bare_version(self):
        assert dev_builds.format_label("5.3.0", None) == "5.3.0"

    def test_record_without_a_digest_still_reports_the_count(self):
        assert dev_builds.format_label("5.3.0", {"count": 2}) == "5.3.0 (dev.2)"

    @pytest.mark.parametrize(
        "record",
        [{}, {"count": 0}, {"count": -1}, {"count": "3"}, {"count": True}, "nope", 5],
    )
    def test_unusable_record_renders_the_bare_version(self, record):
        assert dev_builds.format_label("5.3.0", record) == "5.3.0"

    def test_describe_version_matches_format_label_of_the_stored_record(self, data_dir):
        dev_builds.record_build("5.3.0", "fb27693c")
        assert dev_builds.describe_version("5.3.0") == "5.3.0 (dev.1, build fb27693c)"

    def test_an_untouched_version_renders_exactly_as_before_this_feature(self, data_dir):
        dev_builds.record_build("5.3.0", "fb27693c")
        assert dev_builds.describe_version("5.4.0") == "5.4.0"


# ---------------------------------------------------------------------------
# The five version sources the release gate protects stay untouched
# ---------------------------------------------------------------------------

class TestReleaseGateSourcesUntouched:
    def test_counter_never_writes_inside_the_repo(self, data_dir):
        """The counter is state, not source.

        `bin/pre-publish-validate.js` requires package.json, pyproject.toml,
        .claude-plugin/plugin.json, .claude-plugin/marketplace.json and the
        CHANGELOG header to agree on one version; a counter written into any of
        them would break the release and dirty a git-tracked file per dev run.
        """
        dev_builds.record_build("5.3.0", "fb27693c")

        written = dev_builds.sidecar_path().resolve()
        assert written.is_relative_to(data_dir.resolve())
        assert not written.is_relative_to(_REPO_ROOT)
