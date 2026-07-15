"""
gaia schedule -- the OS-agnostic DESIRED-STATE registry for recurring tasks.

Moves scheduled tasks out of a single machine's crontab and into gaia.db, so any
machine sharing the DB can materialize them. The schedule is stored NEUTRAL (a
JSON schedule_spec: calendar|interval), and a per-platform backend translates it
to the native scheduler (cron today; launchd/schtasks deferred). On WSL a task
lives in the distro's cron, never the Windows Task Scheduler.

Consent model (classified in hooks/modules/security/mutative_verbs.py):
  T0 -- register/add, list, show, status, enable, disable: reversible desired-
        state bookkeeping in gaia.db; never touches the machine scheduler.
  T3 -- sync:   MATERIALIZES desired state into the OS scheduler (writes crontab).
        remove: irreversible desired-state row deletion (reversible path: disable).

Subcommands:
    gaia schedule register --name N (--cron "..."|--every 6h|--spec JSON)
                           [--prompt-file F|--prompt TEXT|--prompt-path P]
                           [--project-dir D] [--machine M ...|--all-machines]
                           [--adopt --match SUBSTR] [--workspace W] [--json]
    gaia schedule add ...        (alias of register)
    gaia schedule list [--all-workspaces] [--workspace W] [--json]
    gaia schedule show <name> [--workspace W] [--json]
    gaia schedule status [--workspace W] [--json]
    gaia schedule enable <name> [--workspace W] [--json]
    gaia schedule disable <name> [--workspace W] [--json]
    gaia schedule remove <name> [--workspace W] [--json]     (T3)
    gaia schedule sync [--workspace W] [--json]              (T3)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_workspace(explicit):
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


def _err(msg, as_json=False):
    if as_json:
        print(json.dumps({"error": msg}))
    else:
        print(f"Error: {msg}", file=sys.stderr)
    return 1


_CRON_FIELD_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
_CRON_FIELD_KEYS = ["minute", "hour", "day_of_month", "month", "day_of_week"]


def _parse_cron_field(tok, lo, hi):
    """Parse one cron field to None (any) | int | sorted list[int]."""
    if tok == "*":
        return None
    vals = set()
    for part in tok.split(","):
        if part.startswith("*/"):
            step = int(part[2:])
            vals.update(range(lo, hi + 1, step))
        elif "-" in part:
            a, b = part.split("-", 1)
            vals.update(range(int(a), int(b) + 1))
        else:
            vals.add(int(part))
    out = sorted(vals)
    if not out:
        return None
    return out[0] if len(out) == 1 else out


def _cron_to_spec(cron_str):
    """Convert a 5-field cron string to a neutral calendar spec dict."""
    toks = cron_str.split()
    if len(toks) != 5:
        raise ValueError(f"cron expression must have 5 fields, got {len(toks)}: {cron_str!r}")
    spec = {"kind": "calendar"}
    for key, tok, (lo, hi) in zip(_CRON_FIELD_KEYS, toks, _CRON_FIELD_RANGES):
        spec[key] = _parse_cron_field(tok, lo, hi)
    return spec


_EVERY_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)


def _every_to_spec(value):
    """Convert '6h' / '30m' / '45s' / '2d' to a neutral interval spec dict."""
    m = _EVERY_RE.match(value)
    if not m:
        raise ValueError(f"--every must look like '6h', '30m', '45s', '2d'; got {value!r}")
    amount = int(m.group(1))
    unit = m.group(2).lower()
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return {"kind": "interval", "every_seconds": amount * mult}


def _build_spec(args):
    """Resolve the neutral schedule_spec from --spec / --cron / --every."""
    provided = [x for x in (args.spec, args.cron, args.every) if x]
    if len(provided) != 1:
        raise ValueError("provide exactly one of --spec, --cron, --every")
    if args.spec:
        spec = json.loads(args.spec)
    elif args.cron:
        spec = _cron_to_spec(args.cron)
    else:
        spec = _every_to_spec(args.every)
    from gaia.schedulers import validate_spec
    validate_spec(spec)
    return spec


def _adopt_from_crontab(match):
    """Find an UNMARKED crontab line matching ``match``; return (cron, dir, pf).

    Reads the current crontab read-only. Returns the 5-field cron expression and
    best-effort PROJECT_DIR / PROMPT_FILE parsed from the line, or None when no
    matching line is found.
    """
    from gaia.schedulers.cron import CronBackend, _MARKER_RE
    for line in CronBackend._read_crontab():
        if _MARKER_RE.search(line):
            continue  # already managed
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue  # blank line or comment -- never a crontab entry
        if match not in line:
            continue
        toks = line.split(None, 5)
        if len(toks) < 6:
            continue
        cron = " ".join(toks[:5])
        rest = toks[5]
        pd = re.search(r"PROJECT_DIR=([^\s]+)", rest)
        pf = re.search(r"PROMPT_FILE=([^\s]+)", rest)
        return cron, (pd.group(1) if pd else None), (pf.group(1) if pf else None)
    return None


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _cmd_register(args):
    from gaia.store.writer import upsert_scheduled_task
    from gaia.schedulers import render_hint

    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))

    project_dir = args.project_dir
    prompt_path = args.prompt_path

    # Adoption: derive schedule + paths from an existing unmarked crontab line.
    if args.adopt:
        if not args.match:
            return _err("--adopt requires --match SUBSTR", as_json=as_json)
        found = _adopt_from_crontab(args.match)
        if found is None:
            return _err(f"no unmarked crontab line matches {args.match!r}", as_json=as_json)
        cron, adopted_dir, adopted_pf = found
        args.cron = args.cron or cron
        project_dir = project_dir or adopted_dir
        prompt_path = prompt_path or adopted_pf

    try:
        spec = _build_spec(args)
    except Exception as exc:
        return _err(str(exc), as_json=as_json)

    # Prompt body (canonical, portable) from --prompt-file / --prompt.
    prompt_body = None
    if args.prompt_file:
        try:
            prompt_body = Path(args.prompt_file).expanduser().read_text(encoding="utf-8")
        except Exception as exc:
            return _err(f"cannot read --prompt-file: {exc}", as_json=as_json)
    elif args.prompt:
        prompt_body = args.prompt

    machine_scope = "named" if args.machine else "all"

    try:
        task_id = upsert_scheduled_task(
            name=args.name,
            schedule_spec=spec,
            schedule_hint=render_hint(spec),
            prompt_body=prompt_body,
            prompt_path=prompt_path,
            project_dir=project_dir,
            machine_scope=machine_scope,
            machines=args.machine or None,
            workspace=workspace,
        )
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps({"status": "ok", "id": task_id, "name": args.name,
                          "workspace": workspace, "spec": spec, "adopted": bool(args.adopt)}))
    else:
        verb = "Adopted" if args.adopt else "Registered"
        print(f"{verb} scheduled task '{args.name}' (#{task_id}) -- {render_hint(spec)}")
        print("Not yet installed on any machine. Run `gaia schedule sync` (T3) to materialize.")
    return 0


def _cmd_list(args):
    from gaia.store.reader import list_scheduled_tasks

    as_json = getattr(args, "json", False)
    all_ws = getattr(args, "all_workspaces", False)
    workspace = None if all_ws else _resolve_workspace(getattr(args, "workspace", None))
    rows = list_scheduled_tasks(workspace=workspace, include_disabled=True)

    if as_json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    if not rows:
        print("No scheduled tasks registered.")
        return 0
    for r in rows:
        state = "enabled" if r.get("enabled") else "disabled"
        scope = r.get("machine_scope")
        if scope == "named":
            scope = "machines: " + ",".join(r.get("machines", []))
        print(f"[{r['id']}] {r['name']} -- {r.get('schedule_hint') or '?'} "
              f"({state}, {scope})")
    return 0


def _cmd_show(args):
    from gaia.store.reader import get_scheduled_task
    from gaia.schedulers import select_backend, machine_name

    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    row = get_scheduled_task(args.name, workspace=workspace)
    if row is None:
        return _err(f"no scheduled task named {args.name!r}", as_json=as_json)

    native = None
    backend = select_backend()
    if backend is not None:
        try:
            native = backend.translate(row)
        except Exception as exc:
            native = f"(cannot translate: {exc})"

    if as_json:
        row["native"] = native
        row["backend"] = backend.name if backend else None
        print(json.dumps(row, indent=2, default=str))
        return 0

    print(f"# Scheduled task #{row['id']}: {row['name']}")
    print(f"workspace:     {row.get('workspace') or '-'}")
    print(f"schedule:      {row.get('schedule_hint') or '?'}")
    print(f"spec:          {json.dumps(row.get('spec', {}))}")
    print(f"enabled:       {bool(row.get('enabled'))}")
    print(f"machine_scope: {row.get('machine_scope')}"
          + (f" ({', '.join(row.get('machines', []))})" if row.get('machine_scope') == 'named' else ""))
    print(f"project_dir:   {row.get('project_dir') or '-'}")
    print(f"prompt_path:   {row.get('prompt_path') or '-'}")
    print(f"prompt_body:   {'(set, ' + str(len(row.get('prompt_body') or '')) + ' chars)' if row.get('prompt_body') else '-'}")
    if backend is not None:
        print(f"\nnative ({backend.name}) on {machine_name()}:\n  {native}")
    return 0


def _render_plan(plan, as_json):
    if as_json:
        out = {
            "machine": plan.machine, "backend": plan.backend,
            "available": plan.available, "in_sync": plan.in_sync,
            "missing": plan.missing, "drift": plan.drift,
            "orphans": plan.orphans, "disabled_present": plan.disabled_present,
            "invalid": plan.invalid,
            "daemon": ({"running": plan.daemon.running, "detail": plan.daemon.detail}
                       if plan.daemon else None),
        }
        print(json.dumps(out, indent=2))
        return 0
    if not plan.available:
        print(f"No scheduler backend available on {plan.machine} "
              f"(only cron/Linux is implemented; launchd/schtasks deferred).")
        return 0
    print(f"# Schedule status on {plan.machine} (backend: {plan.backend})")
    if plan.daemon is not None and plan.daemon.running is False:
        print(f"! scheduler daemon: {plan.daemon.detail}")
    if plan.in_sync:
        print("In sync -- desired state matches the local scheduler.")
    else:
        for m in plan.missing:
            print(f"  MISSING  {m['name']} ({m['expr']}) -- not installed here")
        for d in plan.drift:
            print(f"  DRIFT    {d['name']} -- want [{d['want']}] have [{d['have']}]")
        for n in plan.orphans:
            print(f"  ORPHAN   {n} -- managed here but no longer desired")
        for n in plan.disabled_present:
            print(f"  DISABLED {n} -- disabled but still installed")
        print("Run `gaia schedule sync` (T3) to reconcile.")
    for iv in plan.invalid:
        print(f"  INVALID  {iv['name']} -- {iv['error']}")
    return 0


def _cmd_status(args):
    from gaia.schedulers import compute_plan
    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    plan = compute_plan(workspace=workspace)
    return _render_plan(plan, as_json)


def _cmd_enable(args):
    return _set_enabled(args, True)


def _cmd_disable(args):
    return _set_enabled(args, False)


def _set_enabled(args, enabled):
    from gaia.store.writer import set_scheduled_task_enabled
    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    res = set_scheduled_task_enabled(args.name, enabled, workspace=workspace)
    if res.get("status") == "not_found":
        return _err(f"no scheduled task named {args.name!r}", as_json=as_json)
    if as_json:
        print(json.dumps(res))
    else:
        state = "enabled" if enabled else "disabled"
        print(f"Task '{args.name}' {state}. Run `gaia schedule sync` (T3) to apply on this machine.")
    return 0


def _cmd_remove(args):
    from gaia.store.writer import delete_scheduled_task
    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    res = delete_scheduled_task(args.name, workspace=workspace)
    if res.get("status") == "not_found":
        return _err(f"no scheduled task named {args.name!r}", as_json=as_json)
    if as_json:
        print(json.dumps(res))
    else:
        print(f"Removed scheduled task '{args.name}' from desired state. "
              f"Run `gaia schedule sync` (T3) to drop its scheduler entry.")
    return 0


def _cmd_sync(args):
    from gaia.schedulers import select_backend, machine_name, compute_plan
    from gaia.store.reader import scheduled_tasks_for_machine, get_scheduled_task
    from gaia.store.writer import mark_scheduled_task_state

    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    mach = machine_name()

    backend = select_backend()
    if backend is None or not backend.available():
        return _err(f"no scheduler backend available on {mach}", as_json=as_json)

    # Plan first (what will change), then apply the idempotent whole-block install.
    plan = compute_plan(workspace=workspace)
    desired = scheduled_tasks_for_machine(mach, workspace=workspace)
    prev_managed = set(backend.list_managed())

    try:
        installed = backend.install(desired)
    except Exception as exc:
        return _err(f"sync failed: {exc}", as_json=as_json)

    # Record per-machine state: desired tasks are installed; anything previously
    # managed but no longer installed is marked not-installed.
    for task in desired:
        mark_scheduled_task_state(task["id"], mach, backend=backend.name, installed=True)
    for name in (prev_managed - set(installed)):
        row = get_scheduled_task(name, workspace=workspace)
        if row is not None:
            mark_scheduled_task_state(row["id"], mach, backend=backend.name, installed=False)

    removed = sorted(prev_managed - set(installed))
    if as_json:
        print(json.dumps({"status": "ok", "machine": mach, "backend": backend.name,
                          "installed": installed, "removed": removed,
                          "was_missing": [m["name"] for m in plan.missing],
                          "was_drift": [d["name"] for d in plan.drift]}))
    else:
        print(f"Synced {len(installed)} task(s) into {backend.name} on {mach}.")
        if removed:
            print(f"Removed {len(removed)} orphan/disabled entr(ies): {', '.join(removed)}")
        if not installed and not removed:
            print("Nothing to do -- already in sync.")
    return 0


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(subparsers):
    p = subparsers.add_parser(
        "schedule",
        help="Desired-state registry for recurring tasks (register/list/status/sync)",
        description="Manage OS-agnostic desired state for recurring scheduled tasks.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = p.add_subparsers(dest="schedule_action", metavar="<action>")

    def _add_ws_json(sp):
        sp.add_argument("--workspace", default=None, metavar="W")
        sp.add_argument("--json", action="store_true", default=False)

    # register / add
    for verb in ("register", "add"):
        rp = actions.add_parser(
            verb,
            help="Register or update a desired-state task (T0; does not install)",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog=(
                "Examples:\n"
                "  gaia schedule register --name gmail-triage --cron '20 9,13,17,21 * * *' \\\n"
                "    --prompt-file ./gmail-triage.prompt.md --project-dir /home/jorge/ws/me\n"
                "  gaia schedule register --name nightly --every 6h --prompt 'Do X'\n"
                "  gaia schedule register --name gmail-triage --adopt --match gmail-triage\n"
            ),
        )
        rp.add_argument("--name", required=True, metavar="NAME")
        rp.add_argument("--cron", default=None, help="5-field cron expression")
        rp.add_argument("--every", default=None, help="interval: 6h | 30m | 45s | 2d")
        rp.add_argument("--spec", default=None, help="raw neutral schedule_spec JSON")
        rp.add_argument("--prompt-file", dest="prompt_file", default=None,
                        help="file whose contents become the canonical prompt body")
        rp.add_argument("--prompt", default=None, help="inline prompt body")
        rp.add_argument("--prompt-path", dest="prompt_path", default=None,
                        help="machine-local prompt file path (when body is not stored)")
        rp.add_argument("--project-dir", dest="project_dir", default=None)
        rp.add_argument("--machine", action="append", default=None,
                        help="scope to a named machine (repeatable); default: all machines")
        rp.add_argument("--all-machines", dest="all_machines", action="store_true", default=False)
        rp.add_argument("--adopt", action="store_true", default=False,
                        help="derive schedule from an existing unmarked crontab line")
        rp.add_argument("--match", default=None, help="substring to find the crontab line to adopt")
        _add_ws_json(rp)

    lp = actions.add_parser("list", help="List registered scheduled tasks",
                            formatter_class=argparse.RawDescriptionHelpFormatter)
    lp.add_argument("--all-workspaces", dest="all_workspaces", action="store_true", default=False)
    _add_ws_json(lp)

    shp = actions.add_parser("show", help="Show one task + its native translation",
                             formatter_class=argparse.RawDescriptionHelpFormatter)
    shp.add_argument("name", metavar="NAME")
    _add_ws_json(shp)

    stp = actions.add_parser("status", help="Reconcile desired state vs the local scheduler",
                             formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_ws_json(stp)

    for verb, helptext in (("enable", "Enable a task (T0)"), ("disable", "Disable a task (T0)")):
        ep = actions.add_parser(verb, help=helptext,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
        ep.add_argument("name", metavar="NAME")
        _add_ws_json(ep)

    rmp = actions.add_parser("remove", help="Delete a task from desired state (T3)",
                             formatter_class=argparse.RawDescriptionHelpFormatter)
    rmp.add_argument("name", metavar="NAME")
    _add_ws_json(rmp)

    syp = actions.add_parser("sync", help="Materialize desired state into the OS scheduler (T3)",
                             formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_ws_json(syp)


def cmd_schedule(args):
    action = getattr(args, "schedule_action", None)
    handlers = {
        "register": _cmd_register,
        "add": _cmd_register,
        "list": _cmd_list,
        "show": _cmd_show,
        "status": _cmd_status,
        "enable": _cmd_enable,
        "disable": _cmd_disable,
        "remove": _cmd_remove,
        "sync": _cmd_sync,
    }
    if action in handlers:
        return handlers[action](args)
    print("Usage: gaia schedule <register|list|show|status|enable|disable|remove|sync>",
          file=sys.stderr)
    return 0
