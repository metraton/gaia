# Episodic Capture - Quick Reference

## Installation

Already integrated in `tools/3-clarification/workflow.py` (Phase 0).

## Usage Pattern

```python
from episodic_capture_hook import capture_phase_0, update_phase_3, update_phase_4, update_phase_5

# Phase 0 - Automatic in clarification workflow
result = execute_workflow(user_prompt, ask_user_question_func=AskUserQuestion)
episode_id = result["episode_id"]

# Phase 3 - After agent work
update_phase_3(episode_id, {"tier": "T3", "operations": [...]}, "agent-name")

# Phase 4 - After approval
update_phase_4(episode_id, "approved", "T3", "user feedback")

# Phase 5 - After execution
update_phase_5(episode_id, "success", True, 45.0, ["cmd1", "cmd2"])
```

## Function Signatures

```python
# Phase 0
capture_phase_0(
    original_prompt: str,
    enriched_prompt: str,
    clarification_data: Optional[Dict] = None,
    command_context: Optional[Dict] = None
) -> Optional[str]  # Returns episode_id

# Phase 3
update_phase_3(
    episode_id: str,
    realization_package: Dict[str, Any],  # Must have "tier" and "operations"
    agent_name: Optional[str] = None
) -> bool

# Phase 4
update_phase_4(
    episode_id: str,
    approval_decision: str,  # "approved", "rejected", "modified"
    tier: Optional[str] = None,
    user_feedback: Optional[str] = None
) -> bool

# Phase 5
update_phase_5(
    episode_id: str,
    outcome: str,  # "success", "partial", "failed", "abandoned"
    success: bool,
    duration_seconds: Optional[float] = None,
    commands_executed: Optional[List[str]] = None,
    error_message: Optional[str] = None,
    artifacts: Optional[Dict[str, Any]] = None
) -> bool
```

## Error Handling

All functions fail silently:
- Return `None` or `False` on error
- Log warning to stderr
- Never throw exceptions
- Never stop workflow

## Testing

```bash
# Unit tests
pytest tests/hooks/test_episodic_capture_hook.py -v

# CLI test
python3 hooks/episodic_capture_hook.py 0
python3 hooks/episodic_capture_hook.py 3 --episode-id ep_xxx
python3 hooks/episodic_capture_hook.py 4 --episode-id ep_xxx
python3 hooks/episodic_capture_hook.py 5 --episode-id ep_xxx

# View episodes
python3 tools/4-memory/episodic.py list --limit 5
python3 tools/4-memory/episodic.py search "deployment" --limit 3
```

## Key Features

- Automatic tag extraction (kubernetes, terraform, aws, deployment, etc.)
- Sensitive data redaction (passwords, tokens, secrets)
- Operation classification (read, create, delete, apply)
- Non-blocking (fails silently)
- <50ms per operation

See `README_EPISODIC_CAPTURE.md` for complete documentation.
