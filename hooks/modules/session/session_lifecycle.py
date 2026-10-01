"""Host-neutral session lifecycle: start maintenance and context, per-prompt notices, teardown.

A host's entry script translates its own event and environment into these
inputs and formats the result for its wire; nothing here reads either.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionStart:
    """What a host knows about the session it is starting."""

    session_id: str
    source: str
    is_headless: bool
    pinned_build: Optional[dict]
    workspace_dir: Path
    plugin_channel: bool


@dataclass(frozen=True)
class StartOutcome:
    """What the host delivers: the setup message, the non-empty notices by title, the context."""

    setup_message: Optional[str]
    notices: dict
    context: str


def start_session(start: SessionStart) -> StartOutcome:
    """Run the start maintenance, then build the birth block or, for ``compact``, the refresh."""
    run_start_maintenance(start)

    from modules.core.plugin_setup import mark_data_home, run_first_time_setup

    setup_message = run_first_time_setup()
    if setup_message:
        logger.info("Session setup: %s", setup_message)
    try:
        data_home_notice = mark_data_home()
    except Exception as exc:
        logger.warning("data home marker not written (non-fatal): %s", exc)
        data_home_notice = ""

    upgrade_notice = _reconcile_install(start.plugin_channel)
    notices = {"## Database upgrade": upgrade_notice, "## Data home": data_home_notice}
    shown = {title: text for title, text in notices.items() if text}
    alarms = [f"{title}\n{text}" for title, text in shown.items()]
    return StartOutcome(
        setup_message=setup_message,
        notices=shown,
        context=start_context(start.source, alarms),
    )


def run_start_maintenance(start: SessionStart) -> None:
    """Registry, approval, backup, draft and worktree upkeep; each step fails alone.

    Safe to repeat for the same session: registration replaces its entry and
    every other step acts only on what is stale. The worktree sweep runs after
    registration so this session's fresh heartbeat already protects the
    worktrees it owns.
    """
    from modules.session.session_registry import SessionRegistryError, register_session

    try:
        if start.session_id and start.session_id != "default":
            register_session(
                session_id=start.session_id,
                is_headless=start.is_headless,
                pinned_build=start.pinned_build,
            )
    except SessionRegistryError as exc:
        logger.warning("session_registry register failed (non-fatal): %s", exc)

    try:
        from modules.session.session_registry import cleanup_stale_entries
        removed = cleanup_stale_entries()
        if removed:
            logger.info("session_registry: cleaned %d stale entries", removed)
    except Exception as exc:
        logger.debug("cleanup_stale_entries failed (non-fatal): %s", exc)

    try:
        from modules.security.approval_grants import cleanup_expired_grants
        cleaned = cleanup_expired_grants(force=True)
        if cleaned:
            logger.info(
                "approval_grants: cleaned %d expired/orphan files at SessionStart", cleaned,
            )
    except Exception as exc:
        logger.debug("cleanup_expired_grants failed (non-fatal): %s", exc)

    # Counted, never reaped: a pending may belong to a live session in another
    # window, and only SubagentStop knows when its agent finished without it.
    try:
        from modules.security.approval_cleanup import count_stale_db_pendings
        stale = count_stale_db_pendings()
        if stale:
            logger.info(
                "approval_cleanup: %d DB pending(s) past TTL at SessionStart "
                "(reported only; SubagentStop reaps them)",
                stale,
            )
    except Exception as exc:
        logger.debug("count_stale_db_pendings failed (non-fatal): %s", exc)

    try:
        from modules.session.db_backup import maybe_backup_db
        snapshot = maybe_backup_db()
        if snapshot:
            logger.info("db_backup: created SessionStart snapshot %s", snapshot)
    except Exception as exc:
        logger.debug("maybe_backup_db failed (non-fatal): %s", exc)

    try:
        from modules.session.contract_drafts_gc import gc_contract_drafts
        pruned = gc_contract_drafts()
        if pruned:
            logger.info("contract_drafts_gc: pruned %d stale draft(s) at SessionStart", pruned)
    except Exception as exc:
        logger.debug("gc_contract_drafts failed (non-fatal): %s", exc)

    try:
        from gaia.retention.worktree_collector import sweep_repo_worktrees
        collected = sweep_repo_worktrees(start.workspace_dir)
        if collected:
            logger.info(
                "worktree_collector: collected %d abandoned worktree(s) at SessionStart",
                len(collected),
            )
    except Exception as exc:
        logger.debug("sweep_repo_worktrees failed (non-fatal): %s", exc)


def _reconcile_install(plugin_channel: bool) -> str:
    """Migrate and seed the installed database, and start a plugin's first scan; return the notice.

    The plugin channel never runs ``gaia install``, so its session start is
    where both happen. The scan is detached so a large workspace cannot hold
    the session open.
    """
    from gaia.install_root import (
        InsideManagedWorktree,
        installed_root,
        registered_roots,
        start_first_scan,
    )
    from modules.core.plugin_setup import recorded_in_manifest

    try:
        workspace_root = installed_root()
    except InsideManagedWorktree as exc:
        logger.info("no installed workspace: %s", exc)
        workspace_root = None
    upgrade_notice = ""
    try:
        from modules.session.plugin_upgrade import reconcile_plugin_install
        if workspace_root is not None:
            with recorded_in_manifest():
                upgrade_notice = reconcile_plugin_install(workspace_root)
    except Exception as exc:
        logger.warning("plugin upgrade check failed (non-fatal): %s", exc)
        upgrade_notice = f"Gaia could not check its database at session start: {exc}"

    if workspace_root is not None and plugin_channel and workspace_root not in registered_roots():
        try:
            start_first_scan(workspace_root)
        except Exception as exc:
            logger.warning("first scan could not start (non-fatal): %s", exc)
    return upgrade_notice


def start_context(source: str, alarms: list) -> str:
    """Return the birth block, or after compaction the lighter refresh; "" when building fails.

    Compaction takes the refresh because the birth block's scan, memory and
    environment were already delivered at the true start.
    """
    try:
        if source == "compact":
            from modules.context.compact_context_builder import build_compact_context
            return "\n\n".join(b for b in (*alarms, build_compact_context()) if b)
        from modules.session.session_manifest import build_session_context
        return build_session_context(alarms=alarms, record_injection=True)
    except Exception as exc:
        logger.debug("build_session_context failed (non-fatal): %s", exc)
        return ""


def prompt_context(session_id: str, workspace: Optional[str]) -> str:
    """Refresh the session heartbeat, then return the context a user prompt carries."""
    refresh_heartbeat(session_id)
    return notifications_counter(workspace)


def refresh_heartbeat(session_id: str) -> None:
    """Mark a registered session as still alive; an unregistered one stays absent."""
    try:
        from modules.session.session_registry import touch_session
        touch_session(session_id)
    except Exception as exc:
        logger.debug("touch_session failed (non-fatal): %s", exc)


def notifications_counter(workspace: Optional[str]) -> str:
    """Return a one-line count of notifications due in ``workspace`` and global, or "" when none.

    ``None`` counts every workspace. Advisory: a failure returns "".
    """
    try:
        from gaia.store.reader import count_unread_notifications
        n = count_unread_notifications(workspace)
        if not n:
            return ""
        noun = "notification" if n == 1 else "notifications"
        return f"\U0001F514 {n} {noun} due (`gaia notifications list --unread` to see them)"
    except Exception as exc:
        logger.warning("notifications counter failed (advisory, skipping): %s", exc)
        return ""


def end_session(session_id: Optional[str]) -> None:
    """Remove the session from the registry so liveness filters stop counting it."""
    from modules.session.session_registry import SessionRegistryError, unregister_session

    try:
        if session_id:
            unregister_session(session_id=session_id)
            logger.info("SessionEnd: unregistered session %s", session_id)
    except SessionRegistryError as exc:
        logger.debug("session_registry unregister failed (non-fatal): %s", exc)
