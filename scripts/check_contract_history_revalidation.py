#!/usr/bin/env python3
"""History-revalidation guard for the contract shape SSOT.

Answers one question against REAL data, not a fixture: does the current
``gaia.contract.validator.validate_form`` still return the same verdict for
every ``agent_contract_handoff`` envelope already persisted in
``agent_contract_handoffs.raw_handoff_json``?

Why this exists
---------------
The form validator is the single source of truth for contract shape, and
thousands of terminal rows were written against earlier revisions of it. Any
change to that shape is only safe if it is additive AND conditional: a stored
envelope that predates the change must keep the verdict it had. A unit test
built from a hand-written fixture cannot show that -- it can only show that the
shape the author imagined still validates. This walks the rows that exist.

It is a BEFORE/AFTER instrument, so the load-bearing output is the ``digest``:
a sha256 over one ``<id>:<ok>:<codes>`` line per row, sorted by id. Run it
before touching the validator, keep the digest, run it again after and pass it
as ``--expect-digest``. An identical digest means not one stored envelope
changed verdict; a differing digest means history moved, and ``--json`` names
the rows that reject and with which codes.

Two paths, one core
-------------------
Every row is checked twice: the stored dict handed straight to
``validate_form`` (the path ``gaia contract`` writes through), and the same
dict re-emitted as a fenced ``agent_contract_handoff`` block, re-extracted with
``hooks.modules.agents.contract_validator.parse_contract``, then validated. A
row where the two disagree is reported as a path divergence and fails the run
-- that is the failure mode where a field builds one way and is rejected the
other.

Population
----------
Rows whose ``raw_handoff_json`` is not a contract envelope (no ``agent_status``
key) are counted and excluded from the verdict set: born-at-dispatch
placeholders and minimal degraded-capture rows were never contract envelopes,
so validating them measures nothing. Rows whose JSON does not parse at all are
counted separately.

Read-only by construction: the database is opened ``mode=ro`` and this script
writes nothing, anywhere.

Usage:
    python3 scripts/check_contract_history_revalidation.py
    python3 scripts/check_contract_history_revalidation.py --json
    python3 scripts/check_contract_history_revalidation.py --expect-digest HEX
    python3 scripts/check_contract_history_revalidation.py --db /path/to/gaia.db

Exit codes:
    0  every envelope revalidated identically on both paths (and matched
       --expect-digest when given)
    1  the two paths diverged, or the digest differs from --expect-digest
    2  internal error (no database, unreadable table)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_HOOKS_DIR = _REPO_ROOT / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from gaia.contract.validator import validate_form  # noqa: E402
from modules.agents.contract_validator import parse_contract  # noqa: E402

# Optional envelope keys whose presence in history is worth counting: a shape
# change is only "additive" in the sense that matters if the rows it must not
# disturb genuinely do not carry the new field.
_TRACKED_OPTIONAL_KEYS = ("failure_report",)


def _err(msg: str) -> None:
    print(f"[contract-history] ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def _resolve_db(explicit):
    if explicit:
        return Path(explicit).expanduser()
    from gaia.paths import data_dir

    return Path(data_dir()) / "gaia.db"


def _codes_of(envelope: dict):
    result = validate_form(envelope)
    return result.ok, [code.value for code in result.codes]


def _fence_roundtrip(envelope: dict):
    """Re-emit the envelope as a fence and extract it back.

    The ``_contract_tag`` key ``parse_contract`` stamps on its result is
    dropped so the two paths are compared on identical dicts; a stored envelope
    that already carried the key (it is persisted verbatim on the hook path) is
    stripped on both sides for the same reason.
    """
    body = json.dumps({k: v for k, v in envelope.items() if k != "_contract_tag"})
    parsed = parse_contract(f"```agent_contract_handoff\n{body}\n```\n")
    if parsed is None:
        return None
    parsed.pop("_contract_tag", None)
    return parsed


def scan(db_path: Path, max_id=None) -> dict:
    if not db_path.exists():
        _err(f"no database at {db_path}")
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        if max_id is None:
            rows = conn.execute(
                "SELECT id, agent_state, raw_handoff_json "
                "FROM agent_contract_handoffs ORDER BY id"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, agent_state, raw_handoff_json "
                "FROM agent_contract_handoffs WHERE id <= ? ORDER BY id",
                (max_id,),
            ).fetchall()
    except sqlite3.Error as exc:
        _err(f"cannot read agent_contract_handoffs: {exc}")
    finally:
        if conn is not None:
            conn.close()

    verdicts = []
    per_state = {}
    rejected = []
    diverged = []
    unparseable = 0
    non_envelope = 0
    carries_field = {}

    for row in rows:
        try:
            envelope = json.loads(row["raw_handoff_json"] or "")
        except (json.JSONDecodeError, TypeError):
            unparseable += 1
            continue
        if not isinstance(envelope, dict) or not isinstance(
            envelope.get("agent_status"), dict
        ):
            non_envelope += 1
            continue

        ok, codes = _codes_of(envelope)
        verdicts.append(f"{row['id']}:{int(ok)}:{','.join(codes)}")

        state = row["agent_state"] or "-"
        bucket = per_state.setdefault(state, {"pass": 0, "reject": 0})
        bucket["pass" if ok else "reject"] += 1
        if not ok:
            rejected.append({"id": row["id"], "agent_state": state, "codes": codes})

        for key in _TRACKED_OPTIONAL_KEYS:
            if envelope.get(key) is not None:
                carries_field[key] = carries_field.get(key, 0) + 1

        reparsed = _fence_roundtrip(envelope)
        if reparsed is None:
            diverged.append({"id": row["id"], "reason": "fence did not re-extract"})
            continue
        fence_verdict = _codes_of(reparsed)
        if fence_verdict != (ok, codes):
            diverged.append(
                {
                    "id": row["id"],
                    "reason": "cli/fence verdict differs",
                    "cli": [ok, codes],
                    "fence": [fence_verdict[0], fence_verdict[1]],
                }
            )

    return {
        "db": str(db_path),
        "rows_total": len(rows),
        "envelopes_checked": len(verdicts),
        "non_envelope_rows": non_envelope,
        "unparseable_rows": unparseable,
        "passing": len(verdicts) - len(rejected),
        "rejected": len(rejected),
        "path_divergences": len(diverged),
        "carries_field": carries_field,
        "per_state": per_state,
        "digest": hashlib.sha256("\n".join(verdicts).encode()).hexdigest(),
        "rejected_rows": rejected[:20],
        "diverged_rows": diverged[:20],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Revalidate persisted contract envelopes")
    ap.add_argument("--db", default=None,
                    help="Path to gaia.db (default: gaia.paths.data_dir()/gaia.db)")
    ap.add_argument("--json", action="store_true", help="Machine-readable summary")
    ap.add_argument("--expect-digest", metavar="HEX", default=None,
                    help="Fail when the computed digest differs from this value")
    ap.add_argument("--max-id", type=int, default=None, metavar="N",
                    help=(
                        "Only scan rows with id <= N. The table is append-only and "
                        "live, so a digest taken later covers a LARGER population "
                        "and cannot be compared to an earlier one directly. Capping "
                        "at the highest id that existed when the baseline was taken "
                        "reconstructs that exact population, which is what separates "
                        "'a stored verdict changed' from 'new rows arrived'."
                    ))
    args = ap.parse_args()

    report = scan(_resolve_db(args.db), max_id=args.max_id)
    report["max_id"] = args.max_id
    digest_mismatch = bool(args.expect_digest) and args.expect_digest != report["digest"]
    if args.expect_digest:
        report["expected_digest"] = args.expect_digest
        report["digest_matches"] = not digest_mismatch

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"[contract-history] db={report['db']}")
        print(f"  rows in table          : {report['rows_total']}")
        print(f"  contract envelopes     : {report['envelopes_checked']}")
        print(f"  non-envelope rows      : {report['non_envelope_rows']}")
        print(f"  unparseable rows       : {report['unparseable_rows']}")
        print(f"  revalidate PASS        : {report['passing']}")
        print(f"  revalidate REJECT      : {report['rejected']}")
        print(f"  cli/fence divergences  : {report['path_divergences']}")
        print(f"  envelopes with field   : {report['carries_field']}")
        print(f"  digest                 : {report['digest']}")
        for state in sorted(report["per_state"]):
            counts = report["per_state"][state]
            print(f"    {state:<20} pass={counts['pass']:<6} reject={counts['reject']}")
        if args.expect_digest:
            print(f"  expected digest        : {args.expect_digest}")
            print(f"  digest matches         : {report['digest_matches']}")

    if report["path_divergences"] or digest_mismatch:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
