"""
Tests for `gaia memory get-relevant` and curated slug validation.

The `get-relevant` subcommand renders a compact Workspace Memory block for
SessionStart injection. These tests monkeypatch ``gaia.store.writer`` so
they do not require a real SQLite substrate.

Slug validation tests cover the new curated taxonomy (atom/decision/negative).
"""

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import cli.memory as memory_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(name, type_, desc, updated_at, body=None):
    """Construct a curated-memory row dict shaped like list_memory output."""
    row = {
        "name": name,
        "type": type_,
        "description": desc,
        "updated_at": updated_at,
    }
    if body is not None:
        row["body"] = body
    return row


def _fake_list_memory_factory(rows_by_type):
    """Return a list_memory mock that filters by type from a dict of rows."""
    def _impl(workspace, *, type=None):
        if type is None:
            out = []
            for rows in rows_by_type.values():
                out.extend(rows)
            return out
        return list(rows_by_type.get(type, []))
    return _impl


def _fake_get_memory_factory(rows_by_type):
    """Return a get_memory mock that finds a row by (workspace, name)."""
    def _impl(workspace, name):
        for rows in rows_by_type.values():
            for r in rows:
                if r["name"] == name:
                    return dict(r)
        return None
    return _impl


def _patch_writer(monkeypatch, rows_by_type):
    """Patch gaia.store.writer.{list_memory,get_memory} in-place."""
    from gaia.store import writer as _w
    monkeypatch.setattr(_w, "list_memory",
                        _fake_list_memory_factory(rows_by_type))
    monkeypatch.setattr(_w, "get_memory",
                        _fake_get_memory_factory(rows_by_type))


def _args_get_relevant(**overrides):
    """Build a SimpleNamespace for _cmd_get_relevant with defaults."""
    base = {
        "workspace": "qxo",
        "limit": 8,
        "max_chars": 800,
        "types": None,
        "json": True,
        "func": memory_mod._cmd_get_relevant,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# Test class: get-relevant rendering
# ---------------------------------------------------------------------------

class TestGetRelevantEmpty:
    """When the workspace has no curated rows, the block must be empty."""

    def test_empty_workspace_returns_empty_block(self, monkeypatch, capsys):
        _patch_writer(monkeypatch, {})

        rc = memory_mod._cmd_get_relevant(_args_get_relevant())
        out = capsys.readouterr().out

        assert rc == 0
        payload = json.loads(out)
        assert payload["block"] == ""
        assert payload["items"] == []


class TestGetRelevantQuota:
    """Per-type quota must apply (3 atoms + 3 decisions + 2 negatives)."""

    def test_full_set_returns_top_3_3_2_ordered_desc(self, monkeypatch, capsys):
        """5 of each type -> top 3+3+2 ordered by updated_at DESC."""
        rows = {
            "atom": [
                _make_row(f"atom_t{i}", "atom", f"desc {i}",
                          f"2026-05-{20 - i:02d}T00:00:00Z")
                for i in range(5)  # newest first by construction
            ],
            "decision": [
                _make_row(f"decision_t{i}", "decision", f"desc {i}",
                          f"2026-05-{20 - i:02d}T00:00:00Z")
                for i in range(5)
            ],
            "negative": [
                _make_row(f"negative_t{i}", "negative", f"desc {i}",
                          f"2026-05-{20 - i:02d}T00:00:00Z")
                for i in range(5)
            ],
        }
        _patch_writer(monkeypatch, rows)

        rc = memory_mod._cmd_get_relevant(_args_get_relevant())
        out = capsys.readouterr().out

        assert rc == 0
        payload = json.loads(out)
        items = payload["items"]

        atoms = [i for i in items if i["type"] == "atom"]
        decisions = [i for i in items if i["type"] == "decision"]
        negatives = [i for i in items if i["type"] == "negative"]

        assert len(atoms) == 3, f"Expected 3 atoms, got {len(atoms)}"
        assert len(decisions) == 3
        assert len(negatives) == 2

        # Newest first within each group
        assert atoms[0]["name"] == "atom_t0"
        assert atoms[1]["name"] == "atom_t1"
        assert atoms[2]["name"] == "atom_t2"

        # Block structure
        block = payload["block"]
        assert "## Workspace Memory (qxo)" in block
        assert "Atoms:" in block
        assert "Decisions:" in block
        assert "Negative:" in block


class TestGetRelevantMaxChars:
    """When the rendered block exceeds --max-chars, truncate + overflow line."""

    def test_truncates_and_emits_overflow_marker(self, monkeypatch, capsys):
        # Generate rows whose descriptions are long enough to blow past 200 chars
        rows = {
            "atom": [
                _make_row(f"atom_topic_{i}", "atom",
                          "x" * 60, f"2026-05-{20 - i:02d}T00:00:00Z")
                for i in range(3)
            ],
            "decision": [
                _make_row(f"decision_topic_{i}", "decision",
                          "y" * 60, f"2026-05-{20 - i:02d}T00:00:00Z")
                for i in range(3)
            ],
            "negative": [
                _make_row(f"negative_topic_{i}", "negative",
                          "z" * 60, f"2026-05-{20 - i:02d}T00:00:00Z")
                for i in range(2)
            ],
        }
        _patch_writer(monkeypatch, rows)

        # Tight budget forces truncation. Header alone is ~26 chars; each item
        # is ~80 chars; full 8 items would be ~700 chars. 250 leaves ~3 items.
        rc = memory_mod._cmd_get_relevant(
            _args_get_relevant(max_chars=250)
        )
        out = capsys.readouterr().out

        assert rc == 0
        payload = json.loads(out)
        block = payload["block"]

        assert payload["overflow"] > 0, "Expected some items to be dropped"
        assert "more items" in block, (
            f"Overflow footer missing from block:\n{block}"
        )
        assert len(block) <= 250


class TestGetRelevantTypesFilter:
    """--types filter must restrict to the named subset."""

    def test_only_atoms_and_decisions_when_filtered(self, monkeypatch, capsys):
        rows = {
            "atom": [
                _make_row("atom_a", "atom", "alpha",
                          "2026-05-20T00:00:00Z"),
            ],
            "decision": [
                _make_row("decision_b", "decision", "beta",
                          "2026-05-19T00:00:00Z"),
            ],
            "negative": [
                _make_row("negative_c", "negative", "gamma",
                          "2026-05-18T00:00:00Z"),
            ],
        }
        _patch_writer(monkeypatch, rows)

        rc = memory_mod._cmd_get_relevant(
            _args_get_relevant(types="atom,decision")
        )
        out = capsys.readouterr().out

        assert rc == 0
        payload = json.loads(out)
        types_present = {i["type"] for i in payload["items"]}
        assert types_present == {"atom", "decision"}
        assert "negative_c" not in payload["block"]


# ---------------------------------------------------------------------------
# Test class: slug validation on add
# ---------------------------------------------------------------------------

class TestCuratedSlugValidation:
    """Curated types (atom/decision/negative) enforce slug pattern."""

    def test_invalid_atom_slug_is_rejected(self):
        from gaia.store.writer import _validate_curated_slug

        with pytest.raises(ValueError) as excinfo:
            _validate_curated_slug("badname_no_prefix", "atom")
        assert "atom_" in str(excinfo.value)

    def test_invalid_decision_slug_is_rejected(self):
        from gaia.store.writer import _validate_curated_slug

        with pytest.raises(ValueError):
            _validate_curated_slug("Decision_Mixed_Case", "decision")

    def test_invalid_negative_slug_is_rejected(self):
        from gaia.store.writer import _validate_curated_slug

        with pytest.raises(ValueError):
            _validate_curated_slug("negative-with-dashes", "negative")

    def test_valid_atom_slug_passes(self):
        from gaia.store.writer import _validate_curated_slug
        # Should not raise
        _validate_curated_slug("atom_my_topic_42", "atom")

    def test_valid_decision_slug_passes(self):
        from gaia.store.writer import _validate_curated_slug
        _validate_curated_slug("decision_terraform_vs_pulumi", "decision")

    def test_valid_negative_slug_passes(self):
        from gaia.store.writer import _validate_curated_slug
        _validate_curated_slug("negative_helm_inline_charts", "negative")

    def test_legacy_types_skip_slug_check(self):
        """Legacy types (project/user/feedback) should not be validated."""
        from gaia.store.writer import _validate_curated_slug
        # Legacy types accept any name (back-compat).
        _validate_curated_slug("anything_goes", "project")
        _validate_curated_slug("Mixed-Case-OK", "user")
        _validate_curated_slug("free form", "feedback")


# ---------------------------------------------------------------------------
# Test class: parser registration includes get-relevant
# ---------------------------------------------------------------------------

class TestGetRelevantRegistered:
    def test_get_relevant_is_registered(self):
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="subcommand")
        memory_mod.register(subs)

        mem_parser = subs.choices["memory"]
        nested = None
        for action in mem_parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                nested = action
                break
        assert nested is not None
        assert "get-relevant" in nested.choices

    def test_add_choices_include_curated_types(self):
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="subcommand")
        memory_mod.register(subs)

        mem_parser = subs.choices["memory"]
        nested = None
        for action in mem_parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                nested = action
                break
        add_p = nested.choices["add"]
        type_action = next(
            a for a in add_p._actions if a.dest == "type"
        )
        for t in ("project", "user", "feedback", "atom", "decision", "negative"):
            assert t in type_action.choices, (
                f"add --type missing choice {t!r}"
            )
