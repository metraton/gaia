"""Session management module.

Provides:
- session_manager: Session ID retrieval or generation
- session_registry: Heartbeat-based registry of live sessions
- session_lifecycle: Host-neutral session start, per-prompt notices, teardown
- session_manifest: Session birth block assembly
- session_event_injector: Bound the recent session events injected into a dispatch
- compaction_bracket: The host's compaction window per session
- contract_drafts_gc, db_backup, plugin_upgrade: SessionStart maintenance
- session_context_writer: Session event context file writer
- pending_scanner: Pending-approval formatting
"""

__all__ = []
