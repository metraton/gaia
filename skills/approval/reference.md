# Approval Plan Template

Use this template when presenting a T3 plan for user approval.

```markdown
## Deployment Plan

### Summary (3-5 bullets)
- What will be changed
- Why this change is needed
- What the expected outcome is

### Changes Proposed

**Resources to CREATE:**
- [Resource]: [Description]

**Resources to MODIFY:**
- [Resource]: [What changes] (before → after)

**Resources to DELETE:**
- [Resource]: [Why deletion]

### Validation Results

**Dry-run status:**
- ✅ `[simulation command]` - [result summary]

**Dependencies verified:**
- [Dependency]: Available ✓

### Risk Assessment

**Risk Level:** [LOW | MEDIUM | HIGH | CRITICAL]

**Potential Risks:**
1. [Risk]: [Impact]
   - Mitigation: [How we handle it]

**Rollback Plan:**
- If operation fails: [Rollback steps]
- Recovery time estimate: [time]

### Execution Steps

When approved, will execute:
1. [Step 1]
2. [Step 2]
3. [Step 3]

### Verification Criteria

After execution, these checks MUST pass before emitting COMPLETE:
- `[read-only command] [args]` → [expected output or state]

### Files Affected

**Git changes:**
- Modified: [files]
- Added: [files]
- Deleted: [files]
```
