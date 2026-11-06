"""Replay one corpus through an old and a new Bash validator: transition matrix, changed commands, friction caps, report."""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import corpus

STATES = ("FREE", "SIGNED", "BLOCKED", "UNKNOWN")
CORPORA = ("truth", "history", "approvals", "cases")
PINNED = ("truth", "cases")
DEFAULT_BASE = "origin/feat/gaia-5-7"

Classifier = Callable[[Dict[str, dict]], Dict[str, dict]]


def build_units(table: Path, cases: Path, db: Path, since: Optional[str]) -> Tuple[Dict[str, dict], Dict[str, List[Tuple[str, str]]]]:
    """Distinct units to classify, and each corpus as ``(label, unit key)`` pairs; one command in one context is one unit."""
    units: Dict[str, dict] = {}
    members: Dict[str, List[Tuple[str, str]]] = {name: [] for name in CORPORA}

    def command_unit(corpus_name: str, label: str, command: str, context: str) -> None:
        key = f"{context}\0{command}"
        units.setdefault(key, {"command": command, "context": context})
        members[corpus_name].append((label, key))

    for row in corpus.truth_rows(table):
        units[f"truth\0{row['id']}"] = {"command": row["command"], "row": row}
        members["truth"].append((row["id"], f"truth\0{row['id']}"))
    for case in corpus.curated_cases(cases):
        command_unit("cases", case["id"], case["command"], case.get("context", "none"))
    con = corpus.open_read_only(db)
    try:
        for command in corpus.history_commands(con, since):
            command_unit("history", command, command, "none")
        for command in corpus.approval_commands(con, since):
            command_unit("approvals", command, command, "none")
    finally:
        con.close()
    return units, members


def compare(units: Dict[str, dict], members: Dict[str, List[Tuple[str, str]]], old: Dict[str, dict], new: Dict[str, dict]) -> dict:
    """Per-corpus 4x4 matrix, the units whose verdict changed, and units that errored on either side, which are never a change."""
    matrix = {name: {a: {b: 0 for b in STATES} for a in STATES} for name in CORPORA}
    changes, errors = [], []
    error_counts = {"old": {}, "new": {}}
    missing = {"verdict": "ERROR", "rule": "missing"}
    for name, pairs in members.items():
        for label, key in pairs:
            before, after = old.get(key, missing), new.get(key, missing)
            entry = {"corpus": name, "key": label, "command": units[key]["command"], "old": before["verdict"],
                     "new": after["verdict"], "old_rule": before.get("rule", ""), "new_rule": after.get("rule", "")}
            if entry["old"] in STATES and entry["new"] in STATES:
                matrix[name][entry["old"]][entry["new"]] += 1
                if entry["old"] != entry["new"]:
                    changes.append(entry)
                continue
            errors.append(entry)
            for side in ("old", "new"):
                if entry[side] not in STATES:
                    reason = entry[f"{side}_rule"] or entry[side]
                    error_counts[side][reason] = error_counts[side].get(reason, 0) + 1
    return {"matrix": matrix, "changes": changes, "errors": errors, "error_counts": error_counts,
            "timing": timing(units, old, new), "sizes": {name: len(pairs) for name, pairs in members.items()}}


SIZE_BUCKETS = (200, 1000, 5000, 20000)


def timing(units: Dict[str, dict], old: Dict[str, dict], new: Dict[str, dict]) -> dict:
    """Classification seconds by command length bucket, and the ten slowest units, from the per-unit seconds the workers record."""
    buckets = {f"<={limit}" if limit else f">{SIZE_BUCKETS[-1]}": {"units": 0, "seconds": 0.0} for limit in (*SIZE_BUCKETS, None)}
    slowest = []
    for key, unit in units.items():
        size = len(unit["command"])
        seconds = max(side.get(key, {}).get("seconds") or 0.0 for side in (old, new))
        label = next((f"<={limit}" for limit in SIZE_BUCKETS if size <= limit), f">{SIZE_BUCKETS[-1]}")
        buckets[label]["units"] += 1
        buckets[label]["seconds"] = round(buckets[label]["seconds"] + seconds, 3)
        slowest.append({"size": size, "seconds": seconds, "old": old.get(key, {}).get("verdict"), "new": new.get(key, {}).get("verdict"),
                        "head": unit["command"][:80]})
    slowest.sort(key=lambda row: row["seconds"], reverse=True)
    return {"by_size": buckets, "slowest": slowest[:10]}


def _loosens(change: dict) -> bool:
    return (change["new"] == "FREE") or (change["old"] == "BLOCKED" and change["new"] != "BLOCKED")


def check_caps(result: dict, caps: dict, declared: dict) -> List[str]:
    """Each cap the diff exceeds, as ``name: value > cap``."""
    reviews = declared.get("reviews", {})
    allowed = set(declared.get("truth_rows", []))
    changes = result["changes"]
    asking = [c for c in changes if c["corpus"] == "history" and c["old"] == "FREE" and c["new"] != "FREE"]
    measured = {
        "history_newly_asking_max_count": len(asking),
        "history_newly_asking_max_fraction": len(asking) / max(result["sizes"]["history"], 1),
        "pinned_changes_max_outside_declared": sum(c["corpus"] in PINNED and c["key"] not in allowed for c in changes),
        "loosened_unreviewed_max": sum(_loosens(c) and not (reviews.get(c["key"]) or reviews.get(c["command"])) for c in changes),
        "new_errors_max": sum(e["new"] not in STATES and e["old"] in STATES for e in result["errors"]),
    }
    return [f"{name}: {value:.4g} > {caps[name]}" if isinstance(value, float) else f"{name}: {value} > {caps[name]}"
            for name, value in measured.items() if value > caps[name]]


def _markdown(report: dict) -> str:
    lines = [f"# Validator replay {report['old']} -> {report['new']}", "",
             f"Sizes: {report['sizes']}. Seconds: {report['seconds']}. Caps: {report['caps_file']}.", "",
             "Violations: " + ("; ".join(report["violations"]) or "none"), ""]
    for name in CORPORA:
        lines += [f"## {name} (old rows, new columns)", "", "| old \\ new | " + " | ".join(STATES) + " |",
                  "|---" * (len(STATES) + 1) + "|"]
        lines += [f"| {a} | " + " | ".join(str(report["matrix"][name][a][b]) for b in STATES) + " |" for a in STATES]
        lines.append("")
    lines += [f"## Changes ({len(report['changes'])})", ""]
    lines += [f"- [{c['corpus']}] {c['old']}({c['old_rule']}) -> {c['new']}({c['new_rule']}) `{c['key']}`: `{c['command']}`"
              for c in report["changes"]]
    lines += ["", "## Errors by side and reason (never a change)", "", "| side | reason | units |", "|---|---|---|"]
    lines += [f"| {side} | {reason} | {count} |" for side in ("old", "new") for reason, count in sorted(report["error_counts"][side].items())]
    lines += ["", "## Classification seconds by command length (slower side)", "", "| length | units | seconds |", "|---|---|---|"]
    lines += [f"| {label} | {row['units']} | {row['seconds']} |" for label, row in report["timing"]["by_size"].items()]
    lines += ["", "## Slowest units", "", "| length | seconds | old | new | head |", "|---|---|---|---|---|"]
    lines += [f"| {row['size']} | {row['seconds']} | {row['old']} | {row['new']} | `{row['head'].splitlines()[0] if row['head'] else ''}` |"
              for row in report["timing"]["slowest"]]
    lines += ["", f"## Errors on either side ({len(report['errors'])})", ""]
    lines += [f"- [{c['corpus']}] {c['old']} -> {c['new']} `{c['key']}` ({c['old_rule']}/{c['new_rule']})" for c in report["errors"]]
    return "\n".join(lines) + "\n"


def run(*, caps, out: Path, db: Path, table: Path = corpus.DEFAULT_TABLE, cases: Path = corpus.DEFAULT_CASES,
        declared=None, since: Optional[str] = None, old: Optional[str] = None, repo: Path = corpus.REPO,
        workers: Optional[int] = None, classifiers: Optional[Tuple[Classifier, Classifier]] = None) -> dict:
    """Classify the corpus at *old* (default: merge-base with ``DEFAULT_BASE``) and at the working tree; write report.json and report.md under *out*."""
    out = Path(out).resolve()
    if out == repo.resolve() or repo.resolve() in out.parents:
        raise ValueError(f"the report carries raw commands and may not be written inside the repository: {out}")
    caps_values = json.loads(Path(caps).read_text(encoding="utf-8")) if not isinstance(caps, dict) else caps
    declared_values = json.loads(Path(declared).read_text(encoding="utf-8")) if isinstance(declared, (str, Path)) else (declared or {})
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    units, members = build_units(table, cases, db, since)
    old_name, new_name = old or "stub-old", "working-tree"
    if classifiers is None:
        from . import runner

        old_name = old or runner.merge_base(repo, DEFAULT_BASE)
        old_root = runner.checkout(repo, old_name, out / "checkouts")
        workers = workers or max(1, (os.cpu_count() or 2) // 2)
        classifiers = (lambda u: runner.classify(old_root, u, out / "old", workers),
                       lambda u: runner.classify(repo, u, out / "new", workers))
    with ThreadPoolExecutor(max_workers=2) as pool:
        old_side, new_side = pool.map(lambda classify: classify(units), classifiers)
    result = compare(units, members, old_side, new_side)
    result.update(old=old_name, new=new_name, units=len(units), seconds=round(time.monotonic() - started, 1),
                  caps_file=str(caps) if not isinstance(caps, dict) else "inline", caps=caps_values,
                  violations=check_caps(result, caps_values, declared_values))
    (out / "report.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    (out / "report.md").write_text(_markdown(result), encoding="utf-8")
    return result
