"""
gaia evidence -- Record and inspect per-AC evidence.

Uses the three-tier storage model: small payloads (<= 4096 bytes) are stored
inline in gaia.db; larger artifacts are written to the filesystem at
~/.gaia/evidence/{workspace}/{brief_slug}/{ac_id}/{uuid}.{ext}.

Subcommands:
    gaia evidence add   --brief <slug> --ac <ac_id> --type <type>
                        [--text "..."] [--artifact-file <path>]
                        [--task <task_id>] [--created-by <agent>]
                        [--workspace W] [--json]

    gaia evidence show  <id> [--json]

    gaia evidence list  --brief <slug> [--ac <ac_id>] [--workspace W] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure the gaia package (repo root) is importable regardless of cwd.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_workspace(explicit: str | None) -> str:
    if explicit:
        return explicit
    try:
        from gaia.project import current as _project_current
        ws = _project_current()
        if ws:
            return ws
    except Exception:
        pass
    return "me"


def _err(msg: str, as_json: bool = False) -> int:
    if as_json:
        print(json.dumps({"error": msg}))
    else:
        print(f"Error: {msg}", file=sys.stderr)
    return 1


def _resolve_brief_id(workspace: str, brief_name: str, db_path=None) -> int:
    """Look up the integer brief_id for (workspace, brief_name)."""
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT id FROM briefs WHERE workspace = ? AND name = ?",
            (workspace, brief_name),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"brief '{brief_name}' not found in workspace '{workspace}'"
            )
        return row["id"]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _cmd_add(args) -> int:
    from gaia.evidence.store import (
        EVIDENCE_INLINE_MAX_BYTES,
        EvidenceWriteForbidden,
        insert_evidence,
    )

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = args.brief
    ac_id = args.ac
    ev_type = args.type
    text_val = getattr(args, "text", None)
    artifact_file = getattr(args, "artifact_file", None)
    task_id = getattr(args, "task", None)
    created_by = getattr(args, "created_by", None)
    as_json = getattr(args, "json", False)

    # Validate mutual exclusion before any I/O
    if text_val is not None and artifact_file is not None:
        return _err(
            "--text and --artifact-file are mutually exclusive; supply at most one.",
            as_json,
        )
    if text_val is None and artifact_file is None:
        return _err(
            "One of --text or --artifact-file must be provided.",
            as_json,
        )

    try:
        brief_id = _resolve_brief_id(workspace, brief_name)
    except ValueError as exc:
        return _err(str(exc), as_json)

    # Decide inline vs blob
    final_text: str | None = None
    final_artifact_path: str | None = None
    final_size_bytes: int | None = None

    if text_val is not None:
        payload_bytes = text_val.encode("utf-8")
        final_size_bytes = len(payload_bytes)
        if final_size_bytes <= EVIDENCE_INLINE_MAX_BYTES:
            final_text = text_val
        else:
            # Large text: write to FS as .txt blob
            try:
                brief_slug = brief_name
                from gaia.evidence.fs import write_blob
                blob_path, size = write_blob(
                    workspace, brief_slug, ac_id,
                    payload_bytes, ext=".txt",
                )
                final_artifact_path = str(blob_path)
                final_size_bytes = size
            except Exception as exc:
                return _err(f"blob write failed: {exc}", as_json)
    else:
        # Artifact file path supplied
        src = Path(artifact_file)
        if not src.exists():
            return _err(f"artifact-file not found: {artifact_file}", as_json)
        try:
            data = src.read_bytes()
        except OSError as exc:
            return _err(f"cannot read artifact-file: {exc}", as_json)

        final_size_bytes = len(data)
        ext = src.suffix or ".bin"

        if final_size_bytes <= EVIDENCE_INLINE_MAX_BYTES:
            # Small binary/text: store inline as text if decodable, else blob
            try:
                final_text = data.decode("utf-8")
            except UnicodeDecodeError:
                # Binary content -- must go to FS even if small
                try:
                    from gaia.evidence.fs import write_blob
                    blob_path, size = write_blob(
                        workspace, brief_name, ac_id, data, ext=ext,
                    )
                    final_artifact_path = str(blob_path)
                    final_size_bytes = size
                except Exception as exc:
                    return _err(f"blob write failed: {exc}", as_json)
        else:
            # Large file: always write to FS
            try:
                from gaia.evidence.fs import write_blob
                blob_path, size = write_blob(
                    workspace, brief_name, ac_id, data, ext=ext,
                )
                final_artifact_path = str(blob_path)
                final_size_bytes = size
            except Exception as exc:
                return _err(f"blob write failed: {exc}", as_json)

    try:
        result = insert_evidence(
            workspace,
            brief_id,
            ac_id,
            type=ev_type,
            text=final_text,
            artifact_path=final_artifact_path,
            size_bytes=final_size_bytes,
            task_id=task_id,
            created_by_agent=created_by,
        )
    except EvidenceWriteForbidden as exc:
        return _err(str(exc), as_json)
    except Exception as exc:
        return _err(str(exc), as_json)

    if as_json:
        print(json.dumps(result))
    else:
        ev_id = result.get("id")
        storage = "inline" if result.get("text") is not None else "blob"
        print(
            f"Evidence recorded: id={ev_id}  ac={ac_id}  "
            f"type={ev_type}  storage={storage}  "
            f"brief={brief_name}"
        )
    return 0


def _cmd_show(args) -> int:
    from gaia.evidence.store import get_evidence

    evidence_id = args.id
    as_json = getattr(args, "json", False)

    try:
        ev_id_int = int(evidence_id)
    except (ValueError, TypeError):
        return _err(f"invalid evidence id: {evidence_id!r}", as_json)

    row = get_evidence(ev_id_int)
    if row is None:
        return _err(f"evidence id {ev_id_int} not found", as_json)

    if as_json:
        print(json.dumps(row))
    else:
        print(f"Evidence #{row['id']}")
        print(f"  brief_id:         {row['brief_id']}")
        print(f"  ac_id:            {row['ac_id']}")
        print(f"  type:             {row['type']}")
        print(f"  task_id:          {row.get('task_id') or '(none)'}")
        print(f"  created_at:       {row['created_at']}")
        print(f"  created_by_agent: {row.get('created_by_agent') or '(none)'}")
        print(f"  size_bytes:       {row.get('size_bytes') or '(unknown)'}")
        if row.get("artifact_path"):
            print(f"  artifact_path:    {row['artifact_path']}")
        elif row.get("text") is not None:
            snippet = (row["text"] or "")[:120]
            print(f"  text (snippet):   {snippet!r}")
    return 0


def _cmd_list(args) -> int:
    from gaia.evidence.store import list_evidence_for_ac
    from gaia.store.writer import _connect

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = args.brief
    ac_id_filter = getattr(args, "ac", None)
    as_json = getattr(args, "json", False)

    try:
        brief_id = _resolve_brief_id(workspace, brief_name)
    except ValueError as exc:
        return _err(str(exc), as_json)

    if ac_id_filter:
        rows = list_evidence_for_ac(brief_id, ac_id_filter)
    else:
        # List all evidence for the brief (all ac_ids)
        con = _connect()
        try:
            raw = con.execute(
                "SELECT * FROM evidence WHERE brief_id = ? "
                "ORDER BY ac_id, created_at ASC, id ASC",
                (brief_id,),
            ).fetchall()
            rows = [{k: r[k] for k in r.keys()} for r in raw]
        finally:
            con.close()

    if as_json:
        print(json.dumps(rows))
    else:
        if not rows:
            print(f"No evidence found for brief '{brief_name}'.")
        else:
            for r in rows:
                storage = "blob" if r.get("artifact_path") else "inline"
                print(
                    f"  [{r['id']:4d}]  ac={r['ac_id']:<12}  "
                    f"type={r['type']:<16}  storage={storage:<8}  "
                    f"created={r['created_at']}"
                )
    return 0


# ---------------------------------------------------------------------------
# register (auto-discovered by bin/gaia)
# ---------------------------------------------------------------------------

def register(subparsers) -> None:
    """Register the `evidence` subcommand with the root parser."""
    ev_parser = subparsers.add_parser(
        "evidence",
        help="Record and inspect per-AC evidence (three-tier storage)",
        description=(
            "Manage structured evidence rows for acceptance criteria. "
            "Small payloads are stored inline in gaia.db; "
            "larger artifacts are written to ~/.gaia/evidence/."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    actions = ev_parser.add_subparsers(dest="evidence_action", metavar="<action>")

    # -- add -------------------------------------------------------------------
    add_p = actions.add_parser(
        "add",
        help="Record a new evidence entry",
        description="Insert evidence for an acceptance criterion.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia evidence add --brief my-brief --ac AC-1 --type text --text 'All tests pass'\n"
            "  gaia evidence add --brief my-brief --ac AC-1 --type file --artifact-file /tmp/report.html\n"
            "  gaia evidence add --brief my-brief --ac AC-2 --type command_output --text 'exit 0' --task T1\n"
        ),
    )
    add_p.add_argument("--brief", required=True, metavar="BRIEF",
                       help="Parent brief slug.")
    add_p.add_argument("--ac", required=True, metavar="AC_ID",
                       help="Acceptance-criteria identifier (e.g. AC-1).")
    add_p.add_argument(
        "--type", required=True,
        choices=("text", "file", "command_output", "url", "screenshot"),
        help="Evidence type.",
    )
    add_p.add_argument("--text", default=None, metavar="TEXT",
                       help="Inline text payload (mutually exclusive with --artifact-file).")
    add_p.add_argument("--artifact-file", dest="artifact_file", default=None,
                       metavar="PATH",
                       help="Path to an artifact file (mutually exclusive with --text).")
    add_p.add_argument("--task", default=None, metavar="TASK_ID",
                       help="Opaque task reference (optional).")
    add_p.add_argument("--created-by", dest="created_by", default=None,
                       metavar="AGENT",
                       help="Agent slug that produced this evidence.")
    add_p.add_argument("--workspace", default=None, metavar="W",
                       help="Workspace identity. Default: gaia.project.current() or 'me'.")
    add_p.add_argument("--json", action="store_true", default=False,
                       help="Emit JSON output.")

    # -- show ------------------------------------------------------------------
    show_p = actions.add_parser(
        "show",
        help="Show a single evidence entry",
        description="Display all fields of an evidence row by integer id.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia evidence show 42\n"
            "  gaia evidence show 42 --json\n"
        ),
    )
    show_p.add_argument("id", metavar="ID", help="Evidence integer id.")
    show_p.add_argument("--json", action="store_true", default=False,
                        help="Emit JSON output.")

    # -- list ------------------------------------------------------------------
    list_p = actions.add_parser(
        "list",
        help="List evidence for a brief",
        description=(
            "List all evidence rows for a brief, optionally filtered by AC."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia evidence list --brief my-brief\n"
            "  gaia evidence list --brief my-brief --ac AC-1\n"
            "  gaia evidence list --brief my-brief --json\n"
        ),
    )
    list_p.add_argument("--brief", required=True, metavar="BRIEF",
                        help="Brief slug.")
    list_p.add_argument("--ac", default=None, metavar="AC_ID",
                        help="Filter to a specific AC (optional).")
    list_p.add_argument("--workspace", default=None, metavar="W",
                        help="Workspace identity. Default: gaia.project.current() or 'me'.")
    list_p.add_argument("--json", action="store_true", default=False,
                        help="Emit JSON output.")

def cmd_evidence(args) -> int:
    """Dispatch to the appropriate evidence subcommand. Called by bin/gaia."""
    action = getattr(args, "evidence_action", None)
    handlers = {
        "add":  _cmd_add,
        "show": _cmd_show,
        "list": _cmd_list,
    }
    if action in handlers:
        return handlers[action](args)

    print("Usage: gaia evidence <add|show|list>", file=sys.stderr)
    return 0
