# tools/validation

This folder holds two small helpers that predate the hook-enforced consent model and are kept as importable utilities. `approval_gate.py` builds an `AskUserQuestion` configuration and a text summary for a realization package, checks the user's answer, and appends each decision to `logs/approvals.jsonl`. `validate_skills.py` is a one-off script that scans skill and agent folders and writes a Markdown report on skill format and injection.

Neither helper enforces anything. The `approval_gate.py` docstring says so: "It enforces nothing itself: the caller decides what to run after `process_approval_response`." Consent for a state-changing command is enforced by the PreToolUse hook. `hooks/modules/tools/bash_validator.py` denies a T3 command with an `approval_id`, and the grant is matched in `hooks/modules/security/approval_grants.py`. Commit messages are checked by `hooks/modules/validation/commit_validator.py`, called only from `bash_validator`, not from this folder.

A developer reaches for these helpers by hand. No hook, CLI verb or agent calls them.

## How it is wired in

The folder is consumed by import, and nothing in the repository imports it today. `__init__.py` exports `ApprovalGate`, `request_approval` and `process_approval_response` from `approval_gate.py`. No code or test outside this folder imports either module; `skills/gaia-patterns/reference.md` lists the folder in its inventory. `validate_skills.py` is invoked by name (`python3 tools/validation/validate_skills.py`). It reads `gaia/skills`, `.claude/skills`, `gaia/agents` and `.claude/agents` relative to the current directory and writes `gaia/tools/validation/skills_report.md`, which `.gitignore` excludes.

**What breaks if you get it wrong here.** Wiring `approval_gate` into a real path as if it were a gate. It records a decision but cannot stop a command, so code that treats a `process_approval_response` result as consent bypasses the signature the hook would have asked for.

## What's here

```
__init__.py          re-exports the three approval_gate entry points
approval_gate.py     ApprovalGate: question config, summary, answer check, logs/approvals.jsonl
validate_skills.py   skill format and agent-injection report, run by hand
```

## Conventions

None beyond the code itself. The two files share no structure, and new consent or validation logic belongs in `hooks/modules/security/` or `hooks/modules/validation/`, where the hook runs it.

## See also

- [`skills/security-tiers/SKILL.md`](../../skills/security-tiers/SKILL.md): what T0-T3 mean and which module enforces each verdict.
- [`hooks/modules/validation/commit_validator.py`](../../hooks/modules/validation/commit_validator.py): the commit-message rules the Bash validator applies.
- [`skills/subagent-request-approval/SKILL.md`](../../skills/subagent-request-approval/SKILL.md): how a T3 command is actually requested and signed.
