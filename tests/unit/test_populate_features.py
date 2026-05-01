"""
Fix 2 regression tests: populate_features populates the features table.

Verifies that the three-tier feature detection heuristic works correctly:
  Tier 1 -- features/ directory
  Tier 2 -- feature.json / feature.yaml descriptor files
  Tier 3 -- flags.json / flags.yaml at repo root
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


def _grant(con, table, agent):
    con.execute(
        "INSERT OR REPLACE INTO agent_permissions (table_name, agent_name, allow_write) "
        "VALUES (?, ?, 1)",
        (table, agent),
    )
    con.commit()


def _setup_permissions(db_path):
    from gaia.store.writer import _connect
    con = _connect(db_path)
    for table in ("repos", "features"):
        _grant(con, table, "developer")
    con.close()
    return con


class TestScanFeatures:
    """Unit tests for the internal _scan_features() helper."""

    def test_tier1_features_directory(self, tmp_path):
        """Subdirectories under features/ are detected as feature units."""
        from tools.scan.store_populator import _scan_features

        repo = tmp_path / "my-repo"
        repo.mkdir()
        features_dir = repo / "features"
        for name in ("auth-feature", "orders-feature", "shared"):
            (features_dir / name).mkdir(parents=True)

        result = _scan_features(repo)
        names = {f["name"] for f in result}
        assert "auth-feature" in names
        assert "orders-feature" in names
        assert "shared" in names
        assert len(result) == 3

    def test_tier2_feature_descriptor_json(self, tmp_path):
        """feature.json files anywhere in the repo are detected."""
        from tools.scan.store_populator import _scan_features

        repo = tmp_path / "my-repo"
        (repo / "packages" / "auth").mkdir(parents=True)
        (repo / "packages" / "auth" / "feature.json").write_text('{"name": "auth"}')

        result = _scan_features(repo)
        names = {f["name"] for f in result}
        assert "auth" in names

    def test_tier2_feature_descriptor_yaml(self, tmp_path):
        """feature.yaml files anywhere in the repo are detected."""
        from tools.scan.store_populator import _scan_features

        repo = tmp_path / "my-repo"
        (repo / "modules" / "billing").mkdir(parents=True)
        (repo / "modules" / "billing" / "feature.yaml").write_text("name: billing\n")

        result = _scan_features(repo)
        names = {f["name"] for f in result}
        assert "billing" in names

    def test_tier3_flags_json_at_root(self, tmp_path):
        """Top-level keys in flags.json at repo root become features."""
        from tools.scan.store_populator import _scan_features

        repo = tmp_path / "my-repo"
        repo.mkdir()
        flags = {"feature-a": True, "feature-b": {"enabled": False}, "feature-c": "experiment"}
        (repo / "flags.json").write_text(json.dumps(flags))

        result = _scan_features(repo)
        names = {f["name"] for f in result}
        assert "feature-a" in names
        assert "feature-b" in names
        assert "feature-c" in names

    def test_no_features_returns_empty_list(self, tmp_path):
        """A plain repo with no feature signals returns an empty list."""
        from tools.scan.store_populator import _scan_features

        repo = tmp_path / "plain-repo"
        repo.mkdir()
        (repo / "package.json").write_text("{}")

        result = _scan_features(repo)
        assert result == []

    def test_deduplication_across_tiers(self, tmp_path):
        """A feature name found in multiple tiers appears only once."""
        from tools.scan.store_populator import _scan_features

        repo = tmp_path / "multi-repo"
        repo.mkdir()

        # Tier 1: features/auth-feature directory
        (repo / "features" / "auth-feature").mkdir(parents=True)
        # Tier 2: feature.json with same name (case-insensitive dedup)
        (repo / "features" / "auth-feature" / "feature.json").write_text('{}')

        result = _scan_features(repo)
        names = [f["name"] for f in result]
        # auth-feature should appear exactly once
        assert names.count("auth-feature") == 1

    def test_node_modules_excluded(self, tmp_path):
        """feature.json files inside node_modules are skipped."""
        from tools.scan.store_populator import _scan_features

        repo = tmp_path / "repo"
        repo.mkdir()
        nm_feat = repo / "node_modules" / "some-pkg"
        nm_feat.mkdir(parents=True)
        (nm_feat / "feature.json").write_text("{}")

        result = _scan_features(repo)
        assert result == []


class TestPopulateFeatures:
    """Integration tests: populate_features writes to the store."""

    def test_populate_features_tier1_qxo_layout(self, tmp_db, tmp_path, monkeypatch):
        """Simulates the qxo-monorepo layout: features/ directory with submodules."""
        import subprocess
        from gaia.store.writer import _connect
        from tools.scan.store_populator import populate_repo, populate_features

        _setup_permissions(tmp_db)

        # Build a repo fixture resembling qxo-monorepo
        repo_path = tmp_path / "qxo-monorepo"
        repo_path.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=str(repo_path), check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "https://bitbucket.org/aaxisdigital/qxo.git"],
            cwd=str(repo_path), check=True,
        )
        features_dir = repo_path / "features"
        for fname in ("auth-feature", "orders-feature", "products-feature"):
            (features_dir / fname).mkdir(parents=True)

        # Ensure the repo row exists first
        populate_repo("ws-qxo", repo_path, "developer", db_path=tmp_db)

        result = populate_features("ws-qxo", "qxo-monorepo", repo_path, "developer", db_path=tmp_db)

        con = _connect(tmp_db)
        rows = con.execute(
            "SELECT name FROM features WHERE project = ? AND repo = ?",
            ("ws-qxo", "qxo-monorepo"),
        ).fetchall()
        con.close()

        feat_names = {r["name"] for r in rows}
        assert "auth-feature" in feat_names
        assert "orders-feature" in feat_names
        assert "products-feature" in feat_names
        assert len(feat_names) >= 3, f"Expected at least 3 features, got {feat_names}"

    def test_populate_features_returns_upsert_counts(self, tmp_db, tmp_path, monkeypatch):
        """populate_features returns a dict with 'features' key containing counts."""
        import subprocess
        from tools.scan.store_populator import populate_repo, populate_features

        _setup_permissions(tmp_db)

        repo_path = tmp_path / "test-repo"
        repo_path.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=str(repo_path), check=True)
        (repo_path / "features" / "feat-a").mkdir(parents=True)
        (repo_path / "features" / "feat-b").mkdir(parents=True)

        populate_repo("ws-test", repo_path, "developer", db_path=tmp_db)
        result = populate_features("ws-test", "test-repo", repo_path, "developer", db_path=tmp_db)

        assert "features" in result
        upsert = result["features"].get("upsert", {})
        assert upsert.get("applied", 0) >= 2, (
            f"Expected at least 2 features applied, got result={result}"
        )
