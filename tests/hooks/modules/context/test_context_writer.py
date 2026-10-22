"""
Tests for hooks.modules.context.context_writer.

Validates the write half of the CONTEXT_UPDATE flow: apply_update upserts a
{contract, payload} delta into project_context_contracts in ~/.gaia/gaia.db.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import pytest

from hooks.modules.context.context_writer import (
    _merge_section_payload,
    apply_update,
)


# ---------------------------------------------------------------------------
# Schema bootstrap helper
# ---------------------------------------------------------------------------

PROJECT = "gaia"
PROJECT_ID = "github.com/org/gaia"


def _bootstrap_schema(db_path: Path) -> None:
    """Build the full schema and the one project the writes are about."""
    con = sqlite3.connect(str(db_path))
    con.executescript((Path(__file__).resolve().parents[4] / "gaia" / "store" / "schema.sql").read_text())
    con.execute("INSERT INTO workspaces (name, identity) VALUES ('me', 'me')")
    con.execute(
        "INSERT INTO projects (workspace, name, path, project_identity) VALUES ('me', ?, ?, ?)",
        (PROJECT, "/home/u/ws/gaia", PROJECT_ID),
    )
    con.commit()
    con.close()


def _seed_contract(db_path: Path, workspace: str, contract_name: str, payload) -> None:
    """Seed an existing section payload, as a prior turn or the scanner would leave it."""
    con = sqlite3.connect(str(db_path))
    con.execute(
        "INSERT OR IGNORE INTO workspaces (name, identity, created_at) VALUES (?, ?, ?)",
        (workspace, workspace, "2026-01-01T00:00:00Z"),
    )
    con.execute(
        """
        INSERT OR REPLACE INTO project_context_contracts
            (workspace, contract_name, payload, metadata, updated_at)
        VALUES (?, ?, ?, NULL, ?)
        """,
        (
            workspace,
            contract_name,
            payload if isinstance(payload, str) else json.dumps(payload),
            "2026-01-01T00:00:00Z",
        ),
    )
    con.commit()
    con.close()


def _read_contract(db_path: Path, workspace: str, contract_name: str):
    """The section as a reader of ``workspace`` sees it after the write."""
    from gaia.store.project_context import workspace_sections

    con = sqlite3.connect(str(db_path))
    section = workspace_sections(con, workspace).get(contract_name)
    con.close()
    return section["payload"] if section else None


def _project_rows(db_path: Path, contract_name: str) -> list:
    con = sqlite3.connect(str(db_path))
    rows = con.execute(
        "SELECT project_identity, payload FROM project_context WHERE contract_name = ?",
        (contract_name,),
    ).fetchall()
    con.close()
    return [(identity, json.loads(payload)) for identity, payload in rows]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    """Provide a fresh DB with the contract tables bootstrapped."""
    db = tmp_path / "gaia.db"
    _bootstrap_schema(db)
    return db


# ---------------------------------------------------------------------------
# 1. apply_update
# ---------------------------------------------------------------------------

class TestApplyUpdate:
    def test_inserts_new_row(self, tmp_db: Path):
        update = {"contract": "stack", "payload": {"languages": ["python"]}}
        audit = apply_update(update, "developer", workspace="me", db_path=tmp_db, project=PROJECT)

        assert audit["success"] is True
        assert audit["contract"] == "stack"
        assert (audit["workspace"], audit["project"]) == ("me", PROJECT_ID)
        assert _project_rows(tmp_db, "stack") == [(PROJECT_ID, {"languages": ["python"]})]

    def test_upsert_is_idempotent(self, tmp_db: Path):
        """A second apply for (project, contract) updates payload, no duplicate row."""
        first = {"contract": "stack", "payload": {"languages": ["python"]}}
        second = {"contract": "stack", "payload": {"languages": ["python", "node"]}}

        apply_update(first, "developer", workspace="me", db_path=tmp_db, project=PROJECT)
        apply_update(second, "developer", workspace="me", db_path=tmp_db, project=PROJECT)

        assert _project_rows(tmp_db, "stack") == [
            (PROJECT_ID, {"languages": ["python", "node"]}),
        ], "upsert must not create duplicate rows"

    def test_a_section_with_no_project_is_refused(self, tmp_db: Path):
        audit = apply_update({"contract": "stack", "payload": {"a": 1}}, "developer",
                             workspace="me", db_path=tmp_db)

        assert audit["success"] is False and "names no project" in audit["error"]
        assert _project_rows(tmp_db, "stack") == []

    def test_db_missing_returns_error(self, tmp_path: Path):
        missing_db = tmp_path / "does-not-exist.db"
        audit = apply_update(
            {"contract": "stack", "payload": {}},
            "developer",
            workspace="me",
            db_path=missing_db,
        )
        assert audit["success"] is False
        assert "gaia.db not found" in audit["error"]


# ---------------------------------------------------------------------------
# 4. Merge semantics: a partial payload must not delete sibling keys
# ---------------------------------------------------------------------------

class TestMergeSectionPayload:
    """Unit-level rules of _merge_section_payload, independent of the DB."""

    def test_new_keys_are_added(self):
        merged = _merge_section_payload({"a": 1}, {"b": 2})
        assert merged == {"a": 1, "b": 2}

    def test_unmentioned_keys_are_preserved(self):
        merged = _merge_section_payload({"a": 1, "b": 2}, {"a": 9})
        assert merged == {"a": 9, "b": 2}

    def test_nested_dicts_merge_recursively(self):
        merged = _merge_section_payload(
            {"outer": {"keep": 1, "inner": {"keep": 2, "change": 3}}},
            {"outer": {"inner": {"change": 4}}},
        )
        assert merged == {"outer": {"keep": 1, "inner": {"keep": 2, "change": 4}}}

    def test_scalar_overwrites(self):
        merged = _merge_section_payload({"version": "1.0"}, {"version": "2.0"})
        assert merged == {"version": "2.0"}

    def test_list_replaces_wholesale_and_is_not_unioned(self):
        """A list is atomic: replacing it is what makes drift correction expressible."""
        merged = _merge_section_payload(
            {"releases": [{"name": "orders", "v": "0.53.0"}, {"name": "pay", "v": "1.2.0"}]},
            {"releases": [{"name": "orders", "v": "0.54.0"}]},
        )
        assert merged["releases"] == [{"name": "orders", "v": "0.54.0"}]

    def test_dict_replacing_scalar_and_scalar_replacing_dict(self):
        assert _merge_section_payload({"x": 1}, {"x": {"y": 2}}) == {"x": {"y": 2}}
        assert _merge_section_payload({"x": {"y": 2}}, {"x": 1}) == {"x": 1}

    def test_stored_is_not_mutated(self):
        stored = {"outer": {"keep": 1}}
        _merge_section_payload(stored, {"outer": {"added": 2}})
        assert stored == {"outer": {"keep": 1}}

    def test_none_stored_yields_delta_only(self):
        assert _merge_section_payload(None, {"a": 1}) == {"a": 1}


class TestApplyUpdateMerges:
    """apply_update must merge into the stored section, never replace it.

    Anchored to a measured incident: a partial write to `me|project_identity`
    collapsed a 962-char payload to 91 chars, deleting the `balance`,
    `metraton_github_io` and `context_design_agentic_deployment` entries. They
    stayed lost until a later scan repopulated them.
    """

    IDENTITY = {
        "gaia": {
            "name": "gaia",
            "local_path": "/home/u/ws/gaia",
            "remote_url": "git@github.com:org/gaia.git",
            "description": "curated by an agent",
        },
        "balance": {"name": "balance", "local_path": "/home/u/ws/balance"},
        "metraton_github_io": {"name": "metraton.github.io", "language": "ruby"},
        "context_design_agentic_deployment": {"name": "context-design-agentic-deployment"},
    }

    def test_partial_write_preserves_sibling_entries(self, tmp_db: Path):
        _seed_contract(tmp_db, "me", "project_identity", self.IDENTITY)

        audit = apply_update(
            {"contract": "project_identity", "payload": {"gaia": {"latest_release": "5.3.0"}}},
            "gaia-operator",
            workspace="me",
            db_path=tmp_db,
            project=PROJECT,
        )

        assert audit["success"] is True
        assert audit["write_mode"] == "merge"

        stored = _read_contract(tmp_db, "me", "project_identity")
        assert set(stored) == set(self.IDENTITY), (
            "a partial write must not delete the sibling entries it did not mention"
        )
        assert stored["balance"] == self.IDENTITY["balance"]
        assert stored["metraton_github_io"] == self.IDENTITY["metraton_github_io"]
        assert stored["context_design_agentic_deployment"] == (
            self.IDENTITY["context_design_agentic_deployment"]
        )
        # Within the touched entry, the delta is additive too.
        assert stored["gaia"]["latest_release"] == "5.3.0"
        assert stored["gaia"]["description"] == "curated by an agent"
        assert stored["gaia"]["remote_url"] == "git@github.com:org/gaia.git"

    def test_two_actors_owning_different_keys_both_survive(self, tmp_db: Path):
        """The ownership split promote.py declares: scan-owned vs agent-owned keys."""
        _seed_contract(
            tmp_db, "me", "project_identity",
            {"gaia": {"local_path": "/home/u/ws/gaia", "platform": "github"}},
        )

        apply_update(
            {"contract": "project_identity", "payload": {"gaia": {"description": "the builder"}}},
            "gaia-operator",
            workspace="me",
            db_path=tmp_db,
            project=PROJECT,
        )

        stored = _read_contract(tmp_db, "me", "project_identity")
        assert stored["gaia"] == {
            "local_path": "/home/u/ws/gaia",
            "platform": "github",
            "description": "the builder",
        }

    def test_repeated_partial_writes_accumulate(self, tmp_db: Path):
        for key in ("a", "b", "c"):
            apply_update(
                {"contract": "stack", "payload": {key: {"seen": True}}},
                "developer",
                workspace="me",
                db_path=tmp_db,
                project=PROJECT,
            )

        stored = _read_contract(tmp_db, "me", "stack")
        assert stored == {k: {"seen": True} for k in ("a", "b", "c")}

    def test_first_write_reports_insert_mode(self, tmp_db: Path):
        audit = apply_update(
            {"contract": "stack", "payload": {"languages": ["python"]}},
            "developer",
            workspace="me",
            db_path=tmp_db,
            project=PROJECT,
        )
        assert audit["write_mode"] == "insert"
        assert _read_contract(tmp_db, "me", "stack") == {"languages": ["python"]}

    def test_corrupt_stored_payload_is_replaced_not_fatal(self, tmp_db: Path):
        _seed_contract(tmp_db, "me", "stack", "{not json")

        audit = apply_update(
            {"contract": "stack", "payload": {"languages": ["python"]}},
            "developer",
            workspace="me",
            db_path=tmp_db,
            project=PROJECT,
        )

        assert audit["success"] is True
        assert audit["write_mode"] == "replace"
        assert _read_contract(tmp_db, "me", "stack") == {"languages": ["python"]}

    def test_non_object_stored_payload_is_replaced(self, tmp_db: Path):
        _seed_contract(tmp_db, "me", "stack", ["python"])

        audit = apply_update(
            {"contract": "stack", "payload": {"languages": ["python"]}},
            "developer",
            workspace="me",
            db_path=tmp_db,
            project=PROJECT,
        )

        assert audit["success"] is True
        assert audit["write_mode"] == "replace"
        assert _read_contract(tmp_db, "me", "stack") == {"languages": ["python"]}

    @pytest.mark.parametrize(
        "payload",
        [["x"], "x", 7, None, True],
        ids=["list", "str", "int", "none", "bool"],
    )
    def test_non_object_incoming_payload_is_refused_not_persisted(
        self, tmp_db: Path, payload
    ):
        """The inverse of test_non_object_stored_payload_is_replaced.

        There the NON-OBJECT value was already in the row and had nothing worth
        preserving. Here it ARRIVES as the delta, and the section it would land
        on holds four entries: persisting it would collapse them all, which is
        the same loss the merge fix closed for dict payloads. A list is the
        reported shape and is included on purpose -- a list is not unioned into
        the section, so it is a wholesale replace like any other non-object.
        """
        _seed_contract(tmp_db, "me", "project_identity", self.IDENTITY)

        audit = apply_update(
            {"contract": "project_identity", "payload": payload},
            "gaia-operator",
            workspace="me",
            db_path=tmp_db,
            project=PROJECT,
        )

        assert audit["success"] is False
        assert audit["write_mode"] is None
        assert "JSON object" in audit["error"]
        assert _read_contract(tmp_db, "me", "project_identity") == self.IDENTITY, (
            "a non-object payload must leave the stored section untouched"
        )

    def test_non_object_incoming_payload_is_skipped_before_the_write(self, tmp_db: Path):
        """The envelope path drops the entry at the parser, before any write.

        Both seams guard the same property; this asserts the outer one fires, so
        the entry never reaches the permission query or the write transaction.
        """
        from hooks.modules.agents.contract_validator import parse_update_contracts

        entries = parse_update_contracts(
            {
                "update_contracts": [
                    {"contract": "project_identity", "payload": ["x"]},
                    {"contract": "stack", "payload": {"languages": ["python"]}},
                ]
            }
        )

        assert [e["contract"] for e in entries] == ["stack"]

    def test_merge_does_not_duplicate_the_row(self, tmp_db: Path):
        _seed_contract(tmp_db, "me", "stack", {"a": 1})
        apply_update(
            {"contract": "stack", "payload": {"b": 2}},
            "developer", workspace="me", db_path=tmp_db, project=PROJECT,
        )

        assert _project_rows(tmp_db, "stack") == [(PROJECT_ID, {"a": 1, "b": 2})], (
            "the first project write starts from the workspace row its readers saw"
        )


