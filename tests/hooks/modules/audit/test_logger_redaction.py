"""Audit records must not persist synthetic secret values."""

from __future__ import annotations

import json
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.audit.logger import AuditLogger


SYNTHETIC_SECRET = "synthetic-audit-secret-657"


def test_audit_logger_redacts_command_nested_parameters_and_error_text(tmp_path):
    audit = AuditLogger(log_dir=tmp_path)
    audit.log_execution(
        tool_name="Bash",
        parameters={
            "command": f"client --token {SYNTHETIC_SECRET}",
            "context": {"credential": SYNTHETIC_SECRET, "resource": "diagnostic"},
        },
        result=f"unused result {SYNTHETIC_SECRET}",
        duration=0.01,
    )
    audit.log_error(
        "synthetic.component",
        "synthetic_error",
        f"stderr password={SYNTHETIC_SECRET}",
        {"nested": [f"Authorization: Bearer {SYNTHETIC_SECRET}"]},
    )

    records = [json.loads(line) for line in next(tmp_path.glob("audit-*.jsonl")).read_text().splitlines()]
    serialized = json.dumps(records)

    assert SYNTHETIC_SECRET not in serialized
    assert records[0]["parameters"]["context"]["resource"] == "diagnostic"
    assert "[REDACTED]" in serialized
