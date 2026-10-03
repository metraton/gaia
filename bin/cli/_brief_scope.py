"""Point a coordination command at the workspace that holds the brief it names.

brief, plan, task, ac, evidence and milestone each address a brief by
(workspace, name). Their dispatchers call :func:`follow_brief` once before
running a subcommand, so a brief written from one workspace is read and
extended from any other without ``--workspace``.
"""

from __future__ import annotations


def follow_brief(args, brief_name: str | None) -> str | None:
    """Set ``args.workspace`` to the workspace holding ``brief_name``.

    Applies only when no ``--workspace`` was given: an explicit flag is
    obeyed as written. Returns the error message to report when several
    workspaces hold the name and the resolved one does not, else ``None``.
    """
    if getattr(args, "workspace", None) or not brief_name:
        return None
    from gaia.briefs import AmbiguousBriefName, brief_home
    from gaia.project import cli_workspace

    try:
        args.workspace = brief_home(cli_workspace(None), brief_name)
    except AmbiguousBriefName as exc:
        return str(exc)
    return None
