"""``gaia memory reclassify --from-file`` and ``gaia memory link --from-file`` apply each line on its own."""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "bin", _REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


@pytest.fixture()
def corpus(tmp_path, monkeypatch):
    """Workspace ``ws``: two open threads, a workspace decision pair, and a group lesson."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    from gaia.store.writer import reclassify_memory, upsert_memory
    for name in ("project_thread_a", "project_thread_b"):
        upsert_memory("ws", name, type="project", kind="fact", body=f"{name} body")
        reclassify_memory("ws", name, class_="thread", status="open")
    upsert_memory("ws", "decision_old", type="decision", kind="decision", body="old")
    upsert_memory("ws", "decision_new", type="decision", kind="decision", body="new")
    upsert_memory("ws", "negative_lesson", type="negative", kind="lesson", body="lesson",
                  initiative="branchkinect")


def _run(capsys, *argv, stdin: str | None = None, monkeypatch=None):
    import cli.memory as memory_mod
    parser = argparse.ArgumentParser()
    memory_mod.register(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args(["memory", *argv])
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    rc = args.func(args)
    out = capsys.readouterr()
    return rc, out.out, out.err


def _state(name: str) -> tuple:
    from gaia.store.reader import get_memory_class_status
    from gaia.store.writer import get_memory
    return (get_memory_class_status("ws", name), get_memory("ws", name)["body"])


def test_reclassify_batch_lands_the_good_lines_refuses_the_bad_and_appends_the_note(
        corpus, capsys, tmp_path):
    batch = tmp_path / "closes.tsv"
    batch.write_text(
        "# slug\tclass/status\tnote\n"
        "project_thread_a\tstatus=closed\tclosed by commit c8a155fa\n"
        "decision_old\tstatus=closed\n"
        "project_thread_b\tclass=anchor\n"
        "missing_slug\tstatus=closed\n"
        "project_thread_a\tcolour=red\n",
        encoding="utf-8",
    )

    rc, out, _ = _run(capsys, "reclassify", "--from-file", str(batch), "--from", "ws", "--json")

    report = json.loads(out)
    assert rc == 1
    assert report["status"] == "partial"
    assert report["counts"] == {"reclassified": 2, "refused": 3}
    assert [(r["line"], r["status"], r.get("code")) for r in report["results"]] == [
        (2, "reclassified", None), (3, "refused", "invalid"), (4, "reclassified", None),
        (5, "refused", "invalid"), (6, "refused", "bad_spec")]
    assert "status only applies to class=thread" in report["results"][1]["error"]
    class_status, body = _state("project_thread_a")
    assert (class_status["class"], class_status["status"]) == ("thread", "closed")
    assert body == "project_thread_a body\n\nclosed by commit c8a155fa"
    assert _state("project_thread_b")[0]["status"] is None
    assert _state("decision_old")[1] == "old"


def test_reclassify_batch_reads_stdin_and_refuses_a_slug_beside_it(corpus, capsys, monkeypatch):
    rc, out, _ = _run(capsys, "reclassify", "--from-file", "-", "--from", "ws",
                      stdin="project_thread_b\tstatus=graduated\tgraduated to decision_new\n",
                      monkeypatch=monkeypatch)
    assert rc == 0, out
    assert "1 applied, 0 refused of 1 line(s)" in out
    assert _state("project_thread_b")[0]["status"] == "graduated"

    rc, _, err = _run(capsys, "reclassify", "project_thread_a", "--from-file", "-")
    assert rc == 1 and "--from-file takes no slug" in err


def test_link_batch_refuses_supersedes_across_owners_and_writes_the_rest(
        corpus, capsys, tmp_path):
    from gaia.store.reader import memory_links_for

    batch = tmp_path / "links.tsv"
    batch.write_text(
        "decision_new\tdecision_old\tsupersedes\n"
        "decision_new\tnegative_lesson\tsupersedes\n"
        "project_thread_a\tdecision_new\tgraduated_to\n"
        "decision_new\tdecision_old\tsupersedes\n"
        "decision_new\tdecision_old\n",
        encoding="utf-8",
    )

    rc, out, _ = _run(capsys, "link", "--from-file", str(batch), "--workspace", "ws", "--json")

    report = json.loads(out)
    assert rc == 1
    assert report["counts"] == {"inserted": 2, "noop": 1, "refused": 2}
    assert [(r["line"], r["status"], r.get("code")) for r in report["results"]] == [
        (1, "inserted", None), (2, "refused", "invalid"), (3, "inserted", None),
        (4, "noop", None), (5, "refused", "bad_line")]
    assert "only a row of its own owner and kind" in report["results"][1]["error"]
    edges = {(e["src_name"], e["dst_name"], e["kind"])
             for e in memory_links_for("ws", ["decision_new", "project_thread_a"])}
    assert ("decision_new", "negative_lesson", "supersedes") not in edges
    assert {("decision_new", "decision_old", "supersedes"),
            ("project_thread_a", "decision_new", "graduated_to")} <= edges


def test_link_batch_refuses_delete_and_the_single_form_still_needs_its_kind(corpus, capsys):
    rc, _, err = _run(capsys, "link", "--from-file", "-", "--delete")
    assert rc == 1 and "one signed call at a time" in err

    rc, _, err = _run(capsys, "link", "decision_new", "decision_old")
    assert rc == 1 and "--kind are required" in err
