"""End-to-end ownership trajectory for one BranchKinect memory thread."""

import sqlite3


def test_branchkinect_create_or_append_then_graduate_and_link(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)

    from gaia.paths import db_path
    from gaia.store.writer import (
        _connect,
        insert_memory_link,
        reclassify_memory,
        resolve_project_ref,
        update_memory_field,
        upsert_memory,
    )

    con = _connect(db_path())
    try:
        con.execute("INSERT OR IGNORE INTO workspaces(name) VALUES ('me')")
        con.execute(
            "INSERT INTO projects(workspace, name, project_identity, status) "
            "VALUES ('me', 'branchkinect', 'github.com/example/branchkinect', 'active')"
        )
        con.commit()
    finally:
        con.close()
    # The CLI's --project path delegates to this store resolver; there is no
    # separate public resolve command, so this test exercises the same seam.
    project_ref = resolve_project_ref("me", "branchkinect", db_path=db_path())
    slug = "project_branchkinect_delivery"
    upsert_memory(
        "me", slug, type="project", body="Initial finding",
        project_ref=project_ref, initiative="branchkinect",
    )
    reclassify_memory("me", slug, class_="thread", status="carry_forward")

    # Search found the owning thread, so the next observation appends instead
    # of creating a second carry-forward row.
    update_memory_field("me", slug, "body", "Second finding", append=True)
    upsert_memory(
        "me", "decision_branchkinect_delivery", type="decision",
        body="Delivery complete because the acceptance evidence passed.",
        project_ref=project_ref, initiative="branchkinect",
    )
    reclassify_memory("me", slug, status="graduated")
    insert_memory_link(
        "me", slug, "decision_branchkinect_delivery", "graduated_to"
    )

    con = sqlite3.connect(str(db_path()))
    try:
        live = con.execute(
            "SELECT name, body, status, project_ref, initiative FROM memory "
            "WHERE workspace='me' AND class='thread' AND initiative='branchkinect'"
        ).fetchall()
        links = con.execute(
            "SELECT kind FROM memory_links WHERE workspace='me' AND src_name=?",
            (slug,),
        ).fetchall()
        history = con.execute(
            "SELECT COUNT(*) FROM memory_history WHERE workspace='me' AND name=?",
            (slug,),
        ).fetchone()[0]
    finally:
        con.close()

    assert live == [(
        slug, "Initial finding\n\nSecond finding", "graduated",
        project_ref, "branchkinect",
    )]
    assert links == [("graduated_to",)]
    assert history >= 2
