"""gaia.approvals -- Approval model for user-in-loop, fingerprint-bound, hash-chained T3 operations.

Public surface:
    chain.validate_chain(approval_id, con) -> bool
    chain.verify_fingerprint(approval_id, payload_json, con) -> bool
    chain.ChainTamperError
    chain.insert_event(con, approval_id, event_type, ...) -> int

    store.insert_requested(sealed_payload, *, agent_id, session_id, con=None) -> str
    store.record_event(approval_id, event_type, *, ..., con=None) -> int
    store.get_pending(session_id=None, all_sessions=False, con=None) -> list[dict]
    store.list_pending(all_sessions=False, session_id=None, con=None) -> list[dict]
    store.approve(approval_id, approver_session, *, agent_id=None, con=None) -> None
    store.reject(approval_id, approver_session, *, agent_id=None, con=None) -> None
    store.transition(approval_id, from_status, to_status, ..., con=None) -> None
    store.replay_for_approval(approval_id, con=None) -> list[dict]
"""
