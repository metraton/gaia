"""The user's rows reach the orchestrator's birth; a specialist's kernel carries its scope chain's rows and none of them."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from tests.conftest import copy_bootstrapped_db

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = str(_REPO_ROOT / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from modules.context.kernel_builder import MEMORY_HEADING, build_kernel_context, build_memory_block  # noqa: E402
from modules.session import session_manifest  # noqa: E402

USER_SCOPE = "_gaia_user"
USER_TITLES = {
    "user_pref_progress": ("preference", "Report progress with each task id beside its title."),
    "user_fact_team": ("fact", "Jorge leads an offshore team."),
    "user_pref_english": ("preference", "Versioned text is written in English."),
}
SCOPE_TITLE = "Run the suite with a temporary HOME before committing."
ROW = {
    "contract_id": "a0123456789abcdef.beefcafe0123",
    "agent_id": "a0123456789abcdef",
    "workspace": "me",
    "dispatch_prompt": "fix the build",
    "kernel_sections": '{"role": "primary", "surface": "app_ci"}',
}


def _insert(con, workspace, name, kind, owner_level, title):
    claim = "Fact" if kind == "fact" else "Preference"
    con.execute(
        "INSERT INTO memory (workspace, name, type, description, body, class, kind, owner_level, updated_at) "
        "VALUES (?, ?, ?, ?, 'the why', 'anchor', ?, ?, '2026-09-01T00:00:00Z')",
        (workspace, name, "user" if owner_level == "user" else "decision", f"{claim}: {title}", kind, owner_level),
    )


def _seed(db: Path, *, scope_row: bool = True) -> None:
    con = sqlite3.connect(db)
    try:
        for workspace in (USER_SCOPE, "me"):
            con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES (?, ?)", (workspace, workspace))
        for name, (kind, title) in USER_TITLES.items():
            _insert(con, USER_SCOPE, name, kind, "user", title)
        if scope_row:
            _insert(con, "me", "decision_me_suite_home", "preference", "workspace", SCOPE_TITLE)
        con.commit()
    finally:
        con.close()


def _env(tmp_path, monkeypatch, template, **seed) -> Path:
    data = tmp_path / "data"
    db = copy_bootstrapped_db(template, data / "gaia.db")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(db))
    monkeypatch.chdir(tmp_path)
    _seed(db, **seed)
    return db


def _kernel(tmp_path, db) -> str:
    return build_kernel_context(ROW, agent_name="developer", agents_dir=tmp_path / "agents", db_path=db)


def test_user_rows_reach_birth_as_title_and_ref_but_not_a_specialist_kernel(
    tmp_path, monkeypatch, bootstrapped_db_template,
):
    """Replaces "orchestrator-only rows reach birth, not a specialist kernel", now by owner and not by audience."""
    db = _env(tmp_path, monkeypatch, bootstrapped_db_template)

    birth = session_manifest.build_session_context()
    kernel = _kernel(tmp_path, db)

    for name, (_, title) in USER_TITLES.items():
        assert f'"{title}"  ref=memory:user/{name}' in birth
        assert title not in kernel
        assert name not in kernel
    assert "memory:user/" not in kernel


def test_specialist_kernel_carries_exactly_the_scope_chain_rows(
    tmp_path, monkeypatch, bootstrapped_db_template,
):
    """Replaces "carries exactly the executor row": the kernel holds the workspace's row, scope-marked, and no user row."""
    db = _env(tmp_path, monkeypatch, bootstrapped_db_template)

    kernel = _kernel(tmp_path, db)

    assert f'  "{SCOPE_TITLE}"  scope=workspace  ref=memory:me/decision_me_suite_home' in kernel
    assert f"{MEMORY_HEADING}: me\nkind=preference" in kernel
    assert not any(title in kernel for _, title in USER_TITLES.values())


def test_no_scope_row_means_no_scope_block_even_when_the_user_has_rows(
    tmp_path, monkeypatch, bootstrapped_db_template,
):
    db = _env(tmp_path, monkeypatch, bootstrapped_db_template, scope_row=False)

    assert build_memory_block(db_path=db) == ""
    assert MEMORY_HEADING not in _kernel(tmp_path, db)
