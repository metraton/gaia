"""Cron backend -- the native scheduler on Linux, including WSL.

Translates a neutral schedule_spec to a 5-field cron expression and materializes
tasks as Gaia-managed crontab lines tagged with a `# gaia-schedule:<name>`
marker. Reading the crontab (`crontab -l`) is read-only (T0); installing/removing
(`crontab -`) mutates the user's crontab and is only ever reached through
`gaia schedule sync` (T3).

Materialization per task (all under ~/.gaia/scheduled-tasks, honoring
GAIA_DATA_DIR):
  - the canonical prompt_body is written to <name>.prompt (the wrapper's
    PROMPT_FILE), so a machine sharing the DB gets the prompt even though the
    crontab is local;
  - the shared headless wrapper (skills/scheduled-task/scripts/
    run-scheduled-task.sh) is copied to a stable path and invoked via `env
    TASK_NAME=... PROJECT_DIR=... PROMPT_FILE=... <wrapper>`;
  - the managed crontab line carries the marker so a re-sync rewrites ONLY the
    Gaia block, never a hand-written line.

Idempotency: install() regenerates the ENTIRE managed block, so running it twice
yields an identical crontab. Adoption: an UNMARKED legacy line for a task being
installed (e.g. the pre-existing gmail-triage entry) is replaced in place, never
duplicated.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, List, Mapping, Optional

from .base import (
    DaemonStatus,
    MANAGED_MARKER_PREFIX,
    SchedulerBackend,
    SpecError,
    validate_spec,
)

_MARKER_RE = re.compile(r"#\s*" + re.escape(MANAGED_MARKER_PREFIX) + r"(\S+)\s*$")


def _data_dir() -> Path:
    return Path(os.environ.get("GAIA_DATA_DIR", str(Path("~/.gaia").expanduser()))).expanduser()


def _tasks_dir() -> Path:
    return _data_dir() / "scheduled-tasks"


def _plugin_wrapper_source() -> Optional[Path]:
    """Locate the shipped headless wrapper template.

    This file is <root>/gaia/schedulers/cron.py; the template ships at
    <root>/skills/scheduled-task/scripts/run-scheduled-task.sh in both the source
    tree and the installed plugin (skills + gaia/ both ship).
    """
    root = Path(__file__).resolve().parents[2]
    cand = root / "skills" / "scheduled-task" / "scripts" / "run-scheduled-task.sh"
    return cand if cand.is_file() else None


class CronBackend(SchedulerBackend):
    name = "cron"

    @staticmethod
    def available() -> bool:
        return shutil.which("crontab") is not None

    # -- translation ---------------------------------------------------------

    def translate(self, task: Mapping[str, Any]) -> str:
        """Return the 5-field cron expression for a task's schedule_spec."""
        spec = task.get("spec")
        if spec is None:
            import json
            spec = json.loads(task.get("schedule_spec") or "{}")
        validate_spec(spec)
        return self._spec_to_cron(spec)

    @staticmethod
    def _field(value: Any, star: str = "*") -> str:
        if value is None:
            return star
        if isinstance(value, list):
            return ",".join(str(v) for v in value)
        return str(value)

    def _spec_to_cron(self, spec: Mapping[str, Any]) -> str:
        if spec.get("kind") == "calendar":
            return " ".join([
                self._field(spec.get("minute")),
                self._field(spec.get("hour")),
                self._field(spec.get("day_of_month")),
                self._field(spec.get("month")),
                self._field(spec.get("day_of_week")),
            ])
        # interval
        secs = int(spec["every_seconds"])
        if secs % 60 != 0:
            raise SpecError("cron granularity is 1 minute; every_seconds must be a multiple of 60")
        mins = secs // 60
        if mins <= 59:
            return f"*/{mins} * * * *"
        if mins % 60 == 0:
            hours = mins // 60
            if hours <= 23:
                return f"0 */{hours} * * *"
            if hours % 24 == 0:
                days = hours // 24
                if days <= 31:
                    return f"0 0 */{days} * *"
        raise SpecError(
            f"interval every_seconds={secs} is not expressible as a single cron line"
        )

    # -- crontab I/O ---------------------------------------------------------

    @staticmethod
    def _read_crontab() -> List[str]:
        """Return the current crontab lines (empty list if none / unreadable)."""
        try:
            res = subprocess.run(
                ["crontab", "-l"],
                capture_output=True, text=True, check=False, timeout=15,
            )
        except Exception:
            return []
        if res.returncode != 0:
            # "no crontab for user" -> treat as empty.
            return []
        return res.stdout.splitlines()

    @staticmethod
    def _write_crontab(lines: List[str]) -> None:
        content = "\n".join(lines).rstrip("\n") + "\n"
        subprocess.run(
            ["crontab", "-"],
            input=content, text=True, check=True, timeout=15,
        )

    @staticmethod
    def _marker_name(line: str) -> Optional[str]:
        m = _MARKER_RE.search(line)
        return m.group(1) if m else None

    def managed_entries(self) -> dict:
        """Return {task_name: cron_expr} for every Gaia-managed crontab line.

        Read-only. Lets reconciliation detect schedule DRIFT (the desired
        translate() vs the installed cron_expr), not just presence/absence.
        """
        out: dict = {}
        for line in self._read_crontab():
            n = self._marker_name(line)
            if n:
                # The 5-field cron expression is the first five whitespace tokens.
                parts = line.split(None, 5)
                out[n] = " ".join(parts[:5]) if len(parts) >= 5 else ""
        return out

    def list_managed(self) -> List[str]:
        return list(self.managed_entries().keys())

    def is_installed(self, name: str) -> bool:
        return name in self.managed_entries()

    # -- materialization -----------------------------------------------------

    def _materialize_prompt(self, task: Mapping[str, Any]) -> str:
        """Write the canonical prompt_body to a machine-local file; return path.

        Falls back to an existing prompt_path when there is no body, or "" when
        neither is set (the wrapper then uses its inline default).
        """
        name = task["name"]
        body = task.get("prompt_body")
        if body:
            tdir = _tasks_dir()
            tdir.mkdir(parents=True, exist_ok=True)
            pf = tdir / f"{name}.prompt"
            pf.write_text(body, encoding="utf-8")
            return str(pf)
        return task.get("prompt_path") or ""

    def _ensure_wrapper(self) -> str:
        """Copy the shipped wrapper to a stable per-machine path; return it."""
        tdir = _tasks_dir()
        tdir.mkdir(parents=True, exist_ok=True)
        dest = tdir / "run-scheduled-task.sh"
        src = _plugin_wrapper_source()
        if src is not None:
            shutil.copyfile(src, dest)
            os.chmod(dest, 0o755)
        return str(dest)

    def _build_line(self, task: Mapping[str, Any], wrapper: str, prompt_path: str) -> str:
        name = task["name"]
        cron_expr = self.translate(task)
        project_dir = task.get("project_dir") or str(Path("~").expanduser())
        log = _tasks_dir() / "logs" / f"{name}.log"
        (log.parent).mkdir(parents=True, exist_ok=True)
        env_assigns = [
            f"TASK_NAME={shlex.quote(name)}",
            f"PROJECT_DIR={shlex.quote(project_dir)}",
        ]
        if prompt_path:
            env_assigns.append(f"PROMPT_FILE={shlex.quote(prompt_path)}")
        cmd = (
            "env " + " ".join(env_assigns) + " " + shlex.quote(wrapper)
            + " >> " + shlex.quote(str(log)) + " 2>&1"
        )
        return f"{cron_expr} {cmd} # {MANAGED_MARKER_PREFIX}{name}"

    @staticmethod
    def _looks_adopted(line: str, name: str) -> bool:
        """Heuristic: an UNMARKED legacy line that belongs to ``name``.

        Conservative -- requires the task name as a token AND a scheduled-task
        invocation signature, so unrelated hand-written lines are never dropped.
        Used only to replace a pre-existing entry in place on first sync (no
        duplicate); marked lines are handled separately.
        """
        if _MARKER_RE.search(line):
            return False
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return False  # blank line or comment -- never a crontab entry
        if re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", line) is None:
            return False
        return (".sh" in line) or ("scheduled-task" in line) or ("claude" in line)

    def install(self, tasks: List[Mapping[str, Any]]) -> List[str]:
        install_names = {t["name"] for t in tasks}
        existing = self._read_crontab()

        # Drop the whole managed block (regenerated below) and any unmarked
        # legacy line adopted by a task being installed.
        kept: List[str] = []
        for line in existing:
            if self._marker_name(line):
                continue
            if any(self._looks_adopted(line, n) for n in install_names):
                continue
            kept.append(line)

        wrapper = self._ensure_wrapper()
        managed: List[str] = []
        installed: List[str] = []
        for task in tasks:
            prompt_path = self._materialize_prompt(task)
            managed.append(self._build_line(task, wrapper, prompt_path))
            installed.append(task["name"])

        new_lines = kept + ([""] if kept and managed else []) + managed
        self._write_crontab(new_lines)
        return installed

    def remove(self, names: List[str]) -> List[str]:
        target = set(names)
        existing = self._read_crontab()
        removed: List[str] = []
        kept: List[str] = []
        for line in existing:
            n = self._marker_name(line)
            if n and n in target:
                removed.append(n)
                continue
            kept.append(line)
        self._write_crontab(kept)
        return removed

    # -- daemon health -------------------------------------------------------

    def ensure_daemon(self) -> DaemonStatus:
        """Report whether a cron daemon looks alive. Never starts it."""
        for proc in ("cron", "crond"):
            try:
                res = subprocess.run(
                    ["pgrep", "-x", proc],
                    capture_output=True, text=True, check=False, timeout=10,
                )
            except Exception:
                continue
            if res.returncode == 0:
                return DaemonStatus(running=True, detail=f"{proc} running")
        # pgrep found nothing (or is unavailable). On WSL the daemon frequently
        # is not running because the distro was not started with systemd/init;
        # report unknown-leaning-down without asserting.
        if shutil.which("pgrep") is None:
            return DaemonStatus(running=None, detail="pgrep unavailable; cannot determine")
        return DaemonStatus(
            running=False,
            detail="no cron daemon process found (on WSL, start it: `sudo service cron start`)",
        )
