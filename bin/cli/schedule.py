"""
gaia schedule -- the OS-agnostic DESIRED-STATE registry for recurring tasks.

Moves scheduled tasks out of a single machine's crontab and into gaia.db, so any
machine sharing the DB can materialize them. The schedule is stored NEUTRAL (a
JSON schedule_spec: calendar|interval), and a per-platform backend translates it
to the native scheduler (cron today; launchd/schtasks deferred). On WSL a task
lives in the distro's cron, never the Windows Task Scheduler.

OFF, PERMANENTLY vs OFF, UNTIL: the two are separate states on purpose.
  disable/enable  -- a permanent decision with no deadline. Off until somebody
                     turns it back on.
  suspend/resume  -- off with a DEADLINE. When the deadline passes the task is
                     active again with nothing written anywhere (expiry is a
                     comparison made at read time, not a daemon), and the next
                     session start announces the lapse. `--indefinitely` gives a
                     suspension with no deadline, which is still not `disable`:
                     it is announced at every session start until resumed.
Suspension has two scopes: one task by name, or `--all` for a workspace-wide
switch. Both are DESIRED state -- they survive a reboot, they are readable
without asking the system scheduler, and they reach the machine only through a
consented `gaia schedule sync`.

Consent model (classified in hooks/modules/security/mutative_verbs.py):
  T0 -- register/add, list, show, status, enable, disable, suspend, resume:
        reversible desired-state bookkeeping in gaia.db; never touches the
        machine scheduler.
  T3 -- sync:   MATERIALIZES desired state into the OS scheduler (writes crontab).
        remove: irreversible desired-state row deletion (reversible path: disable).

Subcommands:
    gaia schedule register --name N (--cron "..."|--every 6h|--spec JSON)
                           [--prompt-file F|--prompt TEXT|--prompt-path P]
                           [--project-dir D] [--machine M ...|--all-machines]
                           [--adopt --match SUBSTR] [--workspace W] [--json]
    gaia schedule add ...        (alias of register)
    gaia schedule list [--all-workspaces] [--workspace W] [--json]
    gaia schedule show <name|id> [--workspace W] [--json]
    gaia schedule status [--workspace W] [--json]
    gaia schedule enable <name|id> [--workspace W] [--json]
    gaia schedule disable <name|id> [--workspace W] [--json]
    gaia schedule suspend (<name|id>|--all) (--for 8h|--until WHEN|--indefinitely)
                          [--reason TEXT] [--workspace W] [--json]
    gaia schedule resume (<name|id>|--all) [--workspace W] [--json]
    gaia schedule remove <name|id> [--workspace W] [--json]     (T3)
    gaia schedule sync [--workspace W] [--json]              (T3)

`list` prints a bracketed `[id]` alongside each task's name. Every verb above
that names ONE task accepts either form: a purely numeric argument is looked
up by id FIRST, across every workspace (ids are a single global sequence, not
scoped per workspace), and only falls back to a name lookup when no such id
exists. `list` (without --all-workspaces) also flags when tasks exist in
workspaces it is not showing, instead of a silence that reads as "there is
nothing else" -- confirmed to have produced a false not-found diagnosis.
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


def _resolve_task_ref(ref, workspace):
    """Resolve a task reference to (name, workspace) it is actually stored under.

    `list` prints a bracketed `[id]` that reads as a usable identifier, but
    every verb below resolved by NAME only -- so `gaia schedule remove 3`
    failed with "no scheduled task named '3'" even though task #3 existed.
    ``id`` is a single global sequence, not scoped per workspace, so a purely
    numeric ref is looked up by id FIRST, across every workspace: that is
    strictly more precise than a name+workspace pair, not less, since it names
    one exact row. A numeric ref that matches no id falls through to the name
    path unchanged, so a name that happens to be all-digits still gets the
    ordinary (and still useful) not-found error rather than a silent misread.
    """
    if ref and ref.isdigit():
        from gaia.store.reader import list_scheduled_tasks
        for row in list_scheduled_tasks(workspace=None, include_disabled=True):
            if int(row["id"]) == int(ref):
                return row["name"], row.get("workspace")
    return ref, workspace


def _task_not_found_error(ref, name, workspace, as_json):
    """Error for an unresolved task reference.

    Names the workspace it was looked up in, and -- when the name exists
    under a DIFFERENT workspace -- says so. A bare "not found" that omits a
    sibling workspace is what let a task hidden by `list` (without
    --all-workspaces) read as nonexistent, costing a consented command a
    false diagnosis. A purely numeric ref that matched no id gets its own
    wording, since guessing an id that turned out wrong is a different
    mistake than typing a name that turned out wrong.
    """
    from gaia.store.reader import list_scheduled_tasks
    others = sorted({r.get("workspace") for r in
                     list_scheduled_tasks(workspace=None, include_disabled=True)
                     if r["name"] == name and r.get("workspace") != workspace})
    if ref.isdigit() and name == ref:
        msg = (f"no scheduled task with id {ref} (checked every workspace) or "
               f"named {ref!r} in workspace {workspace!r}")
    else:
        msg = f"no scheduled task named {name!r} in workspace {workspace!r}"
    if others:
        hint = ", ".join(repr(w) for w in others)
        msg += f"; it exists in: {hint} -- pass --workspace=<workspace> to reach it"
    else:
        msg += ". See its [id] and workspace with `gaia schedule list --all-workspaces`"
    return _err(msg, as_json=as_json)


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


def _describe_suspension(susp):
    """One-line render of a suspension: scope, deadline, and time left."""
    scope = susp.get("scope") or "?"
    if susp.get("indefinite"):
        window = "indefinitely (no deadline)"
    elif susp.get("expired"):
        window = f"deadline {susp.get('until')} passed {susp.get('lapsed_ago')} ago"
    else:
        window = f"until {susp.get('until')} ({susp.get('remaining')} left)"
    out = f"{scope} scope, {window}"
    if susp.get("reason"):
        out += f", reason: {susp['reason']}"
    return out


def _describe_state(task):
    """Render (state, note) for one task row.

    ``state`` is the unambiguous word -- active / disabled / suspended -- and
    ``note`` carries what the word alone cannot say: the deadline of a live
    suspension, or the fact that a suspension already LAPSED (in which case the
    state is active again and the record is still waiting to be acknowledged).
    """
    state = task.get("effective_state") or ("enabled" if task.get("enabled") else "disabled")
    susp = task.get("suspension")
    if state == "suspended" and susp:
        return state, _describe_suspension(susp)
    if state == "active" and susp and susp.get("expired"):
        return state, (f"suspension LAPSED {susp.get('lapsed_ago')} ago "
                       f"-- running again; clear with `gaia schedule resume`")
    return state, None


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
        # Bare array, matching every other Gaia `list --json` (notifications,
        # evidence, task, plan, brief) -- the hidden-elsewhere notice below is
        # for the human-readable path, where the false-diagnosis was measured.
        print(json.dumps(rows, indent=2, default=str))
        return 0

    # A workspace-scoped listing can silently omit tasks that exist elsewhere --
    # measured to cause a false "does not exist" diagnosis when whoever read it
    # had no other signal. Count what a full listing would show so this path
    # says so instead of staying quiet about it.
    elsewhere = (0 if all_ws else
                 len(list_scheduled_tasks(workspace=None, include_disabled=True)) - len(rows))

    if not rows:
        print("No scheduled tasks registered.")
    else:
        for r in rows:
            state, note = _describe_state(r)
            scope = r.get("machine_scope")
            if scope == "named":
                scope = "machines: " + ",".join(r.get("machines", []))
            line = (f"[{r['id']}] {r['name']} -- {r.get('schedule_hint') or '?'} "
                    f"({state}, {scope})")
            print(line)
            if note:
                print(f"        {note}")
    if elsewhere:
        print(f"({elsewhere} task(s) also exist in other workspaces, not shown here -- "
              f"see them with `gaia schedule list --all-workspaces`.)")
    return 0


def _cmd_show(args):
    from gaia.store.reader import get_scheduled_task
    from gaia.schedulers import select_backend, machine_name

    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    ref = args.name
    name, resolved_workspace = _resolve_task_ref(ref, workspace)
    row = get_scheduled_task(name, workspace=resolved_workspace)
    if row is None:
        return _task_not_found_error(ref, name, resolved_workspace, as_json)

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

    if ref.isdigit() and name != ref:
        print(f"(resolved id {ref} to task '{name}' in workspace {resolved_workspace!r})")
    print(f"# Scheduled task #{row['id']}: {row['name']}")
    print(f"workspace:     {row.get('workspace') or '-'}")
    print(f"schedule:      {row.get('schedule_hint') or '?'}")
    print(f"spec:          {json.dumps(row.get('spec', {}))}")
    state, note = _describe_state(row)
    print(f"state:         {state}")
    # `enabled` is the PERMANENT switch and `suspension` the time-bounded one --
    # both printed, so neither is mistaken for the other.
    print(f"enabled:       {bool(row.get('enabled'))}")
    susp = row.get("suspension")
    print(f"suspension:    {_describe_suspension(susp) if susp else '-'}")
    # For a LIVE suspension the note repeats the line just printed; only the
    # lapse note (state is active again) adds anything here.
    if note and state != "suspended":
        print(f"               {note}")
    print(f"machine_scope: {row.get('machine_scope')}"
          + (f" ({', '.join(row.get('machines', []))})" if row.get('machine_scope') == 'named' else ""))
    print(f"project_dir:   {row.get('project_dir') or '-'}")
    print(f"prompt_path:   {row.get('prompt_path') or '-'}")
    print(f"prompt_body:   {'(set, ' + str(len(row.get('prompt_body') or '')) + ' chars)' if row.get('prompt_body') else '-'}")
    if backend is not None:
        print(f"\nnative ({backend.name}) on {machine_name()}:\n  {native}")
    return 0


def _render_suspensions(suspensions):
    """Print the suspension section of `status`, lapses first.

    A LAPSE is listed before a live suspension and marked, because it is the
    entry that means something started running again -- the reader needs to see
    it before anything merely still-paused.
    """
    if not suspensions:
        return
    lapsed = [s for s in suspensions if s.get("expired")]
    live = [s for s in suspensions if not s.get("expired")]
    for s in lapsed:
        who = s.get("task_name") or "all tasks"
        resumed = (", ".join(s.get("resumed_names") or [])
                   or "none (still disabled, or held by another suspension)")
        print(f"  LAPSED    {who} -- suspension expired {s.get('lapsed_ago')} ago "
              f"({s.get('until')}); active again: {resumed}")
    for s in live:
        who = s.get("task_name") or "all tasks"
        window = ("indefinitely" if s.get("indefinite")
                  else f"{s.get('remaining')} left, until {s.get('until')}")
        print(f"  SUSPENDED {who} -- {window}")
    if lapsed:
        print("Acknowledge a lapse with `gaia schedule resume "
              "<name>|--all` (T0) -- it does not stop running again, it clears the notice.")
    if live:
        print("Lift a suspension early with `gaia schedule resume <name>|--all` (T0).")


def _render_plan(plan, as_json, suspensions=None):
    if as_json:
        out = {
            "machine": plan.machine, "backend": plan.backend,
            "available": plan.available, "in_sync": plan.in_sync,
            "missing": plan.missing, "drift": plan.drift,
            "orphans": plan.orphans, "disabled_present": plan.disabled_present,
            "suspended_present": plan.suspended_present,
            "suspensions": suspensions or [],
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
    _render_suspensions(suspensions)
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
            print(f"  DISABLED {n} -- disabled (no deadline) but still installed")
        for s in plan.suspended_present:
            window = (f"until {s['until']} ({s['remaining']} left)" if s.get("until")
                      else "indefinitely")
            print(f"  PAUSED   {s['name']} -- suspended {window} but still installed")
        print("Run `gaia schedule sync` (T3) to reconcile.")
    for iv in plan.invalid:
        print(f"  INVALID  {iv['name']} -- {iv['error']}")
    return 0


def _cmd_status(args):
    from gaia.schedulers import compute_plan
    from gaia.store.reader import list_schedule_suspensions
    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    plan = compute_plan(workspace=workspace)
    suspensions = list_schedule_suspensions(workspace=workspace)
    return _render_plan(plan, as_json, suspensions)


def _cmd_enable(args):
    return _set_enabled(args, True)


def _cmd_disable(args):
    return _set_enabled(args, False)


def _set_enabled(args, enabled):
    from gaia.store.writer import set_scheduled_task_enabled
    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    ref = args.name
    name, resolved_workspace = _resolve_task_ref(ref, workspace)
    res = set_scheduled_task_enabled(name, enabled, workspace=resolved_workspace)
    if res.get("status") == "not_found":
        return _task_not_found_error(ref, name, resolved_workspace, as_json)
    if as_json:
        print(json.dumps(res))
    else:
        state = "enabled" if enabled else "disabled"
        note = f" (id {ref} in workspace {resolved_workspace!r})" if ref.isdigit() and name != ref else ""
        print(f"Task '{name}'{note} {state}. Run `gaia schedule sync` (T3) to apply on this machine.")
    return 0


def _resolve_scope(args, workspace, as_json):
    """Return (name, workspace, ref, error_code) for a verb taking NAME or --all.

    The workspace-wide switch must be asked for EXPLICITLY. A bare `suspend`
    with no target does not silently mean "everything" -- suspending every task
    by omission is exactly the kind of accident a mistyped name would cause.

    When NAME is given, it is resolved through ``_resolve_task_ref`` (id or
    plain name) -- ``ref`` is the original, unresolved argument, kept around so
    a not-found error can still say what the caller actually typed.
    """
    ref = getattr(args, "name", None)
    all_scope = getattr(args, "all", False)
    if ref and all_scope:
        return None, workspace, ref, _err("give either NAME or --all, not both", as_json=as_json)
    if not ref and not all_scope:
        return None, workspace, ref, _err("name a task, or pass --all for the workspace-wide switch",
                          as_json=as_json)
    if ref:
        name, workspace = _resolve_task_ref(ref, workspace)
        return name, workspace, ref, None
    return None, workspace, ref, None


def _cmd_suspend(args):
    from gaia.store.reader import is_duration, parse_deadline
    from gaia.store.writer import suspend_scheduled_tasks

    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    name, workspace, ref, err = _resolve_scope(args, workspace, as_json)
    if err is not None:
        return err

    chosen = [x for x in (args.duration, args.until, args.indefinitely) if x]
    if len(chosen) != 1:
        return _err("provide exactly one of --for, --until, --indefinitely", as_json=as_json)

    until = None
    if args.duration:
        if not is_duration(args.duration):
            return _err(f"--for takes a duration like '8h', '3d', '90m'; "
                        f"for a date use --until. got {args.duration!r}", as_json=as_json)
        until = parse_deadline(args.duration)
    elif args.until:
        if is_duration(args.until):
            return _err(f"--until takes a date/datetime like '2026-09-01' or "
                        f"'2026-09-01T18:00:00'; for a duration use --for. "
                        f"got {args.until!r}", as_json=as_json)
        try:
            until = parse_deadline(args.until)
        except ValueError as exc:
            return _err(str(exc), as_json=as_json)
        from datetime import datetime, timezone
        if datetime.fromisoformat(until.rstrip("Z")).replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
            return _err(f"--until resolves to {until}, which has already passed; "
                        f"suspend with no deadline via --indefinitely, or turn it "
                        f"off for good with `gaia schedule disable`. "
                        f"got {args.until!r}", as_json=as_json)

    res = suspend_scheduled_tasks(name=name, until=until,
                                  reason=args.reason, workspace=workspace)
    if res.get("status") == "not_found":
        return _task_not_found_error(ref, name, workspace, as_json)

    if as_json:
        print(json.dumps(res))
        return 0
    who = f"task '{name}'" if name else f"ALL tasks in workspace '{workspace}'"
    if ref and ref.isdigit() and name != ref:
        who += f" (id {ref})"
    when = "indefinitely (no deadline -- resume to lift)" if until is None else f"until {until}"
    print(f"Suspended {who} {when}.")
    print("Desired state only -- the machine scheduler is untouched. "
          "Run `gaia schedule sync` (T3) to stop it on this machine.")
    if until is not None:
        print("It becomes active again on its own at the deadline; "
              "session start will report the lapse.")
    return 0


def _cmd_resume(args):
    from gaia.store.writer import resume_scheduled_tasks

    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    name, workspace, ref, err = _resolve_scope(args, workspace, as_json)
    if err is not None:
        return err

    res = resume_scheduled_tasks(name=name, workspace=workspace)
    if res.get("status") == "not_found":
        return _task_not_found_error(ref, name, workspace, as_json)
    who = f"task '{name}'" if name else f"ALL tasks in workspace '{workspace}'"
    if ref and ref.isdigit() and name != ref:
        who += f" (id {ref})"
    if res.get("status") == "not_suspended":
        if as_json:
            print(json.dumps(res))
        else:
            print(f"No suspension recorded for {who} -- nothing to clear.")
        return 0

    if as_json:
        print(json.dumps(res))
        return 0
    if res.get("was_expired"):
        print(f"Cleared the LAPSED suspension notice for {who} "
              f"(its deadline {res.get('until')} had already passed -- "
              f"it was active again before this command).")
    else:
        print(f"Resumed {who} -- suspension lifted early.")
    print("Desired state only. Run `gaia schedule sync` (T3) to reinstall it "
          "on this machine if a previous sync removed it.")
    return 0


def _cmd_remove(args):
    from gaia.store.writer import delete_scheduled_task
    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    ref = args.name
    name, resolved_workspace = _resolve_task_ref(ref, workspace)
    res = delete_scheduled_task(name, workspace=resolved_workspace)
    if res.get("status") == "not_found":
        return _task_not_found_error(ref, name, resolved_workspace, as_json)
    if as_json:
        print(json.dumps(res))
    else:
        note = f" (id {ref} in workspace {resolved_workspace!r})" if ref.isdigit() and name != ref else ""
        print(f"Removed scheduled task '{name}'{note} from desired state. "
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
    shp.add_argument("name", metavar="NAME|ID",
                     help="task name, or the [id] `list` prints -- id is checked across every workspace")
    _add_ws_json(shp)

    stp = actions.add_parser("status", help="Reconcile desired state vs the local scheduler",
                             formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_ws_json(stp)

    for verb, helptext in (
        ("enable", "Enable a task permanently -- no deadline (T0)"),
        ("disable", "Disable a task permanently -- no deadline (T0)"),
    ):
        ep = actions.add_parser(verb, help=helptext,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
        ep.add_argument("name", metavar="NAME|ID",
                        help="task name, or the [id] `list` prints -- id is checked across every workspace")
        _add_ws_json(ep)

    susp = actions.add_parser(
        "suspend",
        help="Suspend a task, or all tasks, until a deadline (T0)",
        description=(
            "Suspend WITH A DEADLINE -- the difference from `disable`.\n\n"
            "`disable` is permanent: off until somebody turns it back on.\n"
            "`suspend` carries an expiry: when it passes, the task is active\n"
            "again on its own (evaluated when read -- no daemon, no cron entry\n"
            "to manage cron entries) and the next session start reports the\n"
            "lapse, so nothing comes back silently and nothing stays off by\n"
            "being forgotten.\n\n"
            "Scope is explicit: name a task, or pass --all for the\n"
            "workspace-wide switch. Suspension is DESIRED state -- it survives a\n"
            "reboot and is readable without asking the system scheduler. It\n"
            "reaches this machine only through a consented `gaia schedule sync`."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia schedule suspend --all --for 8h --reason 'debugging the pipeline'\n"
            "  gaia schedule suspend gmail-triage --for 3d\n"
            "  gaia schedule suspend gmail-triage --until 2026-09-01\n"
            "  gaia schedule suspend --all --indefinitely\n"
        ),
    )
    susp.add_argument("name", metavar="NAME|ID", nargs="?", default=None,
                      help="task to suspend (name, or the [id] `list` prints); "
                           "omit and pass --all for every task")
    susp.add_argument("--all", action="store_true", default=False,
                      help="workspace-wide switch: suspend every task at once")
    susp.add_argument("--for", dest="duration", default=None, metavar="DURATION",
                      help="deadline as a duration from now: 8h | 3d | 90m | 2w")
    susp.add_argument("--until", default=None, metavar="WHEN",
                      help="deadline as a date: 2026-09-01 | 2026-09-01T18:00:00")
    susp.add_argument("--indefinitely", action="store_true", default=False,
                      help="no deadline (still announced every session start, unlike disable)")
    susp.add_argument("--reason", default=None, metavar="TEXT",
                      help="why it was suspended; shown in status and at session start")
    _add_ws_json(susp)

    res = actions.add_parser(
        "resume",
        help="Lift a suspension, or acknowledge a lapsed one (T0)",
        description=(
            "Clear the suspension on a task, or the workspace-wide switch.\n\n"
            "Serves both endings a suspension has. On a LIVE suspension it lifts\n"
            "it early. On a LAPSED one -- deadline already passed, tasks already\n"
            "active again -- it acknowledges the notice, which is what stops\n"
            "session start from repeating it. Resuming never reinstalls anything\n"
            "on the machine; `gaia schedule sync` (T3) does that."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia schedule resume gmail-triage\n"
            "  gaia schedule resume --all\n"
        ),
    )
    res.add_argument("name", metavar="NAME|ID", nargs="?", default=None,
                     help="task to resume (name, or the [id] `list` prints); "
                          "omit and pass --all for the workspace switch")
    res.add_argument("--all", action="store_true", default=False,
                     help="clear the workspace-wide suspension")
    _add_ws_json(res)

    rmp = actions.add_parser("remove", help="Delete a task from desired state (T3)",
                             formatter_class=argparse.RawDescriptionHelpFormatter)
    rmp.add_argument("name", metavar="NAME|ID",
                     help="task name, or the [id] `list` prints -- id is checked across every workspace")
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
        "suspend": _cmd_suspend,
        "resume": _cmd_resume,
        "remove": _cmd_remove,
        "sync": _cmd_sync,
    }
    if action in handlers:
        return handlers[action](args)
    print("Usage: gaia schedule "
          "<register|list|show|status|enable|disable|suspend|resume|remove|sync>",
          file=sys.stderr)
    return 0
