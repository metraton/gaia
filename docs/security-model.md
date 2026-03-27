# GAIA Security Model

## T3 Approval Flow (Ops Mode)

When GAIA is installed with the orchestrator:
1. Subagent attempts T3 command -- Hook DENIES (exit 0, permissionDecision=deny) with approval_id
2. Subagent reports REVIEW -- Orchestrator presents via AskUserQuestion
3. User approves -- UserPromptSubmit hook activates grant
4. Subagent retries -- Grant found -- Command passes through (no second dialog)

**This flow is GAIA-controlled and cannot be bypassed by --dangerously-skip-permissions.**

The grant passthrough (step 4) is the key design decision: when GAIA has approved
a command via the orchestrator's approval workflow, the pre_tool_use hook returns
`allowed=True` immediately. There is no intermediate "ask" dialog. GAIA is the
sole authority for T3 approval in ops mode.

## T3 Handling (Security Mode)

When GAIA Security plugin is installed without orchestrator:
- T3 commands return `permissionDecision: "ask"` (Claude Code native dialog)
- **Limitation**: `--dangerously-skip-permissions` can bypass this
- This is a known limitation documented here

Security mode is a fallback for environments where the full orchestrator is not
deployed. It still detects mutative commands and presents them to the user, but
relies on Claude Code's native permission dialog rather than GAIA's approval
workflow.

## What each mode guarantees

| Guarantee | Ops Mode | Security Mode |
|-----------|:--------:|:-------------:|
| T3 commands blocked | YES (exit 0, deny) | YES (ask dialog) |
| User approval required | YES (AskUserQuestion) | YES (native dialog) |
| Bypass-proof | YES | NO (--dangerously-skip-permissions) |
| Concatenation detection | YES | YES |
| Single-use grants | YES | N/A |

## Concatenation Detection

Both modes detect rm and destructive commands inside compound expressions:

- `cd /tmp && rm -rf test` -- shell parser splits, rm component denied
- `find . | xargs rm -rf` -- shell parser splits, rm component denied
- `echo start && rm -rf dir && echo done` -- rm component denied
- `python3 -c "import shutil; shutil.rmtree('dir')"` -- inline code analysis
- `(rm -rf /tmp/test)` -- mutative verb detected through parentheses

The shell parser splits on `|`, `&&`, `||`, `;`, and `\n`. Each component is
independently checked against blocked_commands and mutative_verbs.

## Grant Lifecycle

1. **Pending**: write_pending_approval() creates a pending file with nonce, command, session_id
2. **Activated**: activate_pending_approval() converts pending to active grant (one-time operation)
3. **Passthrough**: check_approval_grant() finds active grant, hook returns allowed=True
4. **Confirmed**: post_tool_use confirm_grant() marks grant as confirmed after execution
5. **Consumed**: Grant is single-use; confirmed grants are not reused

## Permanently Blocked Commands

Some commands are permanently blocked (exit 2) regardless of mode or grants:
- `kubectl delete namespace`
- `terraform destroy` (without -target)
- `rm -rf /` and `rm -rf ~`
- Cloud resource deletion (VPCs, databases, clusters, etc.)

These are enforced by blocked_commands.py and cannot be approved through any workflow.
