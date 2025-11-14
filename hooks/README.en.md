# Gaia-Ops Hooks

**[🇪🇸 Versión en español](README.md)**

Hooks are interception points that allow validating and auditing operations before and after execution. They're like security guards who check every action.

## 🎯 Purpose

Hooks ensure operations comply with security policies and are auditable. They provide an automatic protection layer without requiring constant manual intervention.

**Problem it solves:** Without hooks, dangerous operations could execute without validation. Hooks intercept commands and automatically apply security rules, blocking unauthorized operations.

## 🔄 How It Works

### Architecture Flow

```
Agent attempts to execute command
        ↓
[pre_tool_use.py] ← intercepts BEFORE
        ↓
    Validates operation
    ┌───────┴───────┐
    ↓               ↓
 ALLOWED        BLOCKED
    ↓               ↓
Command executes  ERROR + log
    ↓
[post_tool_use.py] ← intercepts AFTER
        ↓
Audits result
        ↓
Log to .claude/logs/
```

### Real Example Flow

```
Example: Agent attempts "kubectl apply -f deployment.yaml"

1. [gitops-operator] generates command:
   kubectl apply -f deployment.yaml
   ↓
2. [pre_tool_use.py] intercepts:
   - Detects: kubectl apply (T3 operation)
   - Classifies: write_operation, production
   - Consults: settings.json permissions
   ↓
3. [PolicyEngine] evaluates:
   - Tier: T3 (execution)
   - Requires: user_approval
   - Current state: no_approval_yet
   ↓
4. Decision: BLOCK temporarily
   ↓
5. [Approval Gate] activates:
   - Shows proposed changes
   - User reviews: deployment.yaml
   - User approves: ✅
   ↓
6. [pre_tool_use.py] allows execution
   ↓
7. [kubectl] executes:
   deployment.apps/auth configured
   ↓
8. [post_tool_use.py] audits:
   - Timestamp: 2025-11-14 10:23:45
   - Command: kubectl apply
   - Exit code: 0
   - Output: deployment configured
   - Approved by: user@example.com
   ↓
9. Log saved to:
   .claude/logs/2025-11-14-audit.jsonl
```

## 📋 Available Hooks

### Pre-Execution Hooks

**`pre_tool_use.py`** (~400 lines) - Main guardian, validates ALL operations  
**`pre_phase_hook.py`** (~200 lines) - Validates Phase 0-6 transitions  
**`pre_kubectl_security.py`** (~180 lines) - Kubernetes-specific validation  

### Post-Execution Hooks

**`post_tool_use.py`** (~300 lines) - Audits ALL operations  
**`post_phase_hook.py`** (~150 lines) - Audits phase transitions  

### Lifecycle Hooks

**`session_start.py`** (~100 lines) - Executes at session start  
**`subagent_stop.py`** (~120 lines) - Executes when subagent finishes  

## 🚀 How Hooks Work

### Automatic Invocation

Claude Code invokes hooks automatically - no manual call required:

```
Agent → pre_tool_use.py → VALIDATE → ALLOW/BLOCK
                            ↓
                      If ALLOW:
                            ↓
                      Execute command
                            ↓
Agent ← post_tool_use.py ← AUDIT
```

### Permission Configuration

Hooks read `.claude/settings.json` for decisions:

```json
{
  "security_tiers": {
    "T0": {"approval_required": false},
    "T3": {"approval_required": true}
  },
  "always_blocked": ["rm -rf /"],
  "ask_permissions": ["kubectl delete"]
}
```

### Audit Logs

All hooks write to `.claude/logs/`:

```bash
# View today's logs
cat .claude/logs/$(date +%Y-%m-%d)-audit.jsonl | jq .

# Search T3 operations
cat .claude/logs/*.jsonl | jq 'select(.tier == "T3")'
```

## 🔧 Technical Details

### Security Tiers

| Tier | Operation Type | Requires Approval | Validation Hook |
|------|----------------|-------------------|-----------------|
| **T0** | Read-only | No | pre_tool_use |
| **T1** | Validation | No | pre_tool_use |
| **T2** | Planning | No | pre_tool_use |
| **T3** | Execution | **Yes** ✅ | pre_tool_use + pre_phase |

### Hook Structure

```python
def execute_hook(context: dict) -> dict:
    """
    Returns:
        {
            "action": "allow" | "block" | "ask",
            "reason": "Explanation",
            "metadata": {}
        }
    """
```

## 📖 References

**Hook files:**
```
hooks/
├── pre_tool_use.py        (~400 lines)
├── post_tool_use.py       (~300 lines)
├── pre_phase_hook.py      (~200 lines)
├── post_phase_hook.py     (~150 lines)
├── pre_kubectl_security.py (~180 lines)
├── session_start.py       (~100 lines)
└── subagent_stop.py       (~120 lines)
```

**Related tests:**
- `tests/integration/test_hooks_integration.py` (~55 tests)
- `tests/integration/test_hooks_workflow.py` (~19 tests)

---

**Version:** 1.0.0  
**Last updated:** 2025-11-14  
**Total hooks:** 7 hooks  
**Test coverage:** ~120 tests  
**Maintained by:** Gaia (meta-agent) + security team

