# Episodic Capture Hook

Automatic episodic memory capture throughout gaia-ops workflow phases.

## What is this?

This hook automatically captures workflow execution as episodic memory entries. Episodes track:
- Original and enriched prompts (Phase 0)
- Agent realization packages (Phase 3)
- Approval decisions (Phase 4)
- Final outcomes with success/failure (Phase 5)

Every user interaction becomes a searchable memory that can enhance future requests.

## Where This Fits

```
User prompt → Phase 0 (capture_phase_0) → Episode created
             ↓
Agent work → Phase 3 (update_phase_3) → Episode updated with agent details
             ↓
User approval → Phase 4 (update_phase_4) → Episode updated with decision
             ↓
Execution → Phase 5 (update_phase_5) → Episode finalized with outcome
```

## Quick Start

### In Python Code

```python
from episodic_capture_hook import capture_phase_0, update_phase_3, update_phase_4, update_phase_5

# Phase 0: After prompt enrichment
episode_id = capture_phase_0(
    original_prompt="deploy graphql-server",
    enriched_prompt="Deploy graphql-server v1.0.177 to digital-eks-prod",
    clarification_data={"clarification_occurred": True, "ambiguity_score": 55},
    command_context={"command": "deployment"}
)

# Phase 3: After agent generates plan
update_phase_3(
    episode_id=episode_id,
    realization_package={"tier": "T3", "operations": [...]},
    agent_name="devops-agent"
)

# Phase 4: After user approval
update_phase_4(
    episode_id=episode_id,
    approval_decision="approved",
    tier="T3"
)

# Phase 5: After execution
update_phase_5(
    episode_id=episode_id,
    outcome="success",
    success=True,
    duration_seconds=45.0,
    commands_executed=["kubectl apply -f deployment.yaml"]
)
```

### CLI Testing

```bash
# Create episode (Phase 0)
python3 hooks/episodic_capture_hook.py 0

# Update with agent work (Phase 3)
python3 hooks/episodic_capture_hook.py 3 --episode-id ep_xxx

# Update with approval (Phase 4)
python3 hooks/episodic_capture_hook.py 4 --episode-id ep_xxx

# Finalize with outcome (Phase 5)
python3 hooks/episodic_capture_hook.py 5 --episode-id ep_xxx
```

## Integration Points

### 1. Clarification Workflow (tools/3-clarification/workflow.py)

Already integrated. Returns `episode_id` in workflow result:

```python
from clarification import execute_workflow

result = execute_workflow(
    user_prompt="check the API",
    ask_user_question_func=AskUserQuestion
)

episode_id = result["episode_id"]  # Use for later phases
```

### 2. Agent Orchestrator (tools/9-agent-framework/agent_orchestrator.py)

Add to Phase 3 (after agent generates realization package):

```python
from episodic_capture_hook import update_phase_3

# After agent completes realization
update_phase_3(
    episode_id=workflow_context.get("episode_id"),
    realization_package=agent_result,
    agent_name=agent.name
)
```

### 3. Approval Gate (wherever approval happens)

Add to Phase 4:

```python
from episodic_capture_hook import update_phase_4

# After user provides approval
update_phase_4(
    episode_id=workflow_context.get("episode_id"),
    approval_decision="approved" if approved else "rejected",
    tier=realization_package["tier"],
    user_feedback=user_comment
)
```

### 4. Execution Manager (after commands execute)

Add to Phase 5:

```python
from episodic_capture_hook import update_phase_5

# After execution completes
update_phase_5(
    episode_id=workflow_context.get("episode_id"),
    outcome="success" if all_succeeded else "failed",
    success=all_succeeded,
    duration_seconds=elapsed_time,
    commands_executed=executed_commands_list,
    artifacts={"deployments": deployed_versions}
)
```

## Functions

### `capture_phase_0(original_prompt, enriched_prompt, clarification_data, command_context)`

Creates initial episode when prompt is enriched.

**Args:**
- `original_prompt`: User's original request
- `enriched_prompt`: Clarified/enriched version
- `clarification_data`: Optional clarification details
- `command_context`: Optional command context

**Returns:** `episode_id` (string) or `None` if failed

### `update_phase_3(episode_id, realization_package, agent_name)`

Updates episode with agent's realization package.

**Args:**
- `episode_id`: Episode ID from Phase 0
- `realization_package`: Agent's plan (must include "tier" and "operations")
- `agent_name`: Name of agent that generated the package

**Returns:** `True` if successful, `False` otherwise

### `update_phase_4(episode_id, approval_decision, tier, user_feedback)`

Updates episode with user approval decision.

**Args:**
- `episode_id`: Episode ID from Phase 0
- `approval_decision`: "approved", "rejected", or "modified"
- `tier`: Security tier (T0-T3)
- `user_feedback`: Optional user comments

**Returns:** `True` if successful, `False` otherwise

### `update_phase_5(episode_id, outcome, success, duration_seconds, commands_executed, error_message, artifacts)`

Finalizes episode with execution outcome.

**Args:**
- `episode_id`: Episode ID from Phase 0
- `outcome`: "success", "partial", "failed", or "abandoned"
- `success`: Boolean indicating overall success
- `duration_seconds`: Total time taken
- `commands_executed`: List of commands that ran
- `error_message`: Optional error message if failed
- `artifacts`: Optional dict of produced artifacts

**Returns:** `True` if successful, `False` otherwise

## Helper Functions

### `get_memory()` → `EpisodicMemory`

Returns EpisodicMemory instance for custom operations.

### `_extract_tags(original_prompt, enriched_prompt, command_context)` → `List[str]`

Extracts relevant tags from prompts (kubernetes, terraform, aws, deployment, etc.).

### `_sanitize_context(context)` → `Dict`

Removes sensitive data (passwords, tokens) and limits data size.

### `_classify_operations(operations)` → `List[str]`

Classifies operations into categories (read, create, delete, apply, etc.).

## Error Handling

All functions fail silently (return `False` or `None`) to avoid disrupting workflow. Warnings are logged to stderr but don't stop execution.

## Storage

Episodes are stored in `.claude/project-context/episodic-memory/`:
- `episodes/episode-{id}.json` - Individual episode files
- `episodes.jsonl` - Append-only audit log
- `index.json` - Fast search index

## Searching Episodes

Use the episodic memory API to search captured episodes:

```python
from episodic_capture_hook import get_memory

memory = get_memory()
episodes = memory.search_episodes("deployment failed", max_results=5)

for ep in episodes:
    print(f"{ep['title']}: {ep['outcome']}")
```

## Testing

Run comprehensive tests:

```bash
pytest tests/hooks/test_episodic_capture_hook.py -v
```

Tests cover:
- All 4 phases (0, 3, 4, 5)
- Tag extraction
- Context sanitization
- Operation classification
- Full workflow lifecycle
- Error cases

## Security

- Sensitive data (passwords, tokens, API keys) is automatically redacted
- Large data is truncated to prevent storage bloat
- Episodes are stored locally and never transmitted externally

## Performance

- Non-blocking: All operations are fast (<50ms typically)
- Minimal overhead: <1% of total workflow time
- Silent failure: Never disrupts workflow if memory unavailable
