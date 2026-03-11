# Business Logic Redistribution Plan

## Current State

All security validation and context injection happens in `pre_tool_use.py`:

- `approval_grants.check_approval_grant()` -- called inside `BashValidator.validate()` pipeline
  (via `modules/security/approval_grants.py`). When a dangerous verb is detected, the validator
  checks for an active approval before blocking. Also, `activate_pending_approval()` is called
  from `_handle_resume_approval()` when APPROVE:{nonce} tokens arrive via Task resume.

- `context_provider.py` -- invoked via `subprocess.run()` in `_inject_project_context()`
  for every new Agent/Task invocation (not resume). Runs as a separate Python process with
  a 15-second timeout, producing filtered JSON context per agent type.

- `cleanup_expired_grants()` -- called at hook entry for periodic housekeeping.

## Target State

Business logic remains in PreToolUse. The PermissionRequest hook was removed
because the approval grant checking in PreToolUse's BashValidator pipeline is
the authoritative security gate. The PermissionRequest hook was redundant --
it only returned ASK (passthrough to native dialog) and never reached the
auto-approve path in production.

| Logic | Hook | Notes |
|-------|------|-------|
| `check_approval_grant` | PreToolUse (via BashValidator) | Primary security gate, unchanged |
| `activate_pending_approval` | PreToolUse (Task resume) | Nonce activation intercepts Task resume prompts |
| Context injection (per-agent) | PreToolUse (subprocess) | Per-agent filtered context with surface routing |
| UserPromptSubmit | settings.json (static echo) | Identity reinforcement only |

## Files Modified

| File | Change |
|------|--------|
| `hooks/permission_request.py` | **Removed** -- redundant with PreToolUse approval checking |
| `hooks/user_prompt_submit.py` | **Removed** -- simplified to static echo in settings.json |
| `hooks/pre_tool_use.py` | **Unchanged** -- remains the primary security gate |
