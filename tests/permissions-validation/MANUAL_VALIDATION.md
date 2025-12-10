# Manual Permissions Validation Guide

Generated: 2025-12-09 12:45:11

This guide provides step-by-step instructions to manually validate all permission rules.

## 1. ALLOW Rules - Should Execute Automatically

These commands should execute WITHOUT asking for approval.

**Expected behavior:** Commands run immediately and return results.

### Test 1: BashOutput
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 2: ExitPlanMode
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 3: Glob
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 4: Grep
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 5: KillShell
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 6: Read
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 7: Skill
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 8: SlashCommand
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 9: Task
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 10: TodoWrite
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Operation requires approval - should be in 'ask'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 11: WebFetch
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 12: WebSearch
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 13: mcp__ide__executeCode
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 14: mcp__ide__getDiagnostics
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 15: Edit(/tmp/*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Operation requires approval - should be in 'ask'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 16: Write(/tmp/*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Operation requires approval - should be in 'ask'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 17: NotebookEdit
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Operation requires approval - should be in 'ask'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 18: Bash(echo:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 19: Bash(cat:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 20: Bash(ls:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 21: Bash(pwd:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 22: Bash(cd:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 23: Bash(head:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 24: Bash(tail:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 25: Bash(grep:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 26: Bash(find:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 27: Bash(which:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 28: Bash(whoami:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 29: Bash(hostname:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 30: Bash(date:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 31: Bash(uname:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 32: Bash(env:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 33: Bash(printenv:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 34: Bash(wc:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 35: Bash(sort:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 36: Bash(uniq:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 37: Bash(diff:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 38: Bash(file:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 39: Bash(stat:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 40: Bash(realpath:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 41: Bash(dirname:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 42: Bash(basename:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 43: Bash(tree:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 44: Bash(du:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 45: Bash(df:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 46: Bash(free:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 47: Bash(uptime:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 48: Bash(ps:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 49: Bash(top:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 50: Bash(htop:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 51: Bash(id:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 52: Bash(groups:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 53: Bash(getent:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 54: Bash(locale:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 55: Bash(timedatectl:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 56: Bash(lsb_release:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 57: Bash(uname:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 58: Bash(arch:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 59: Bash(nproc:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 60: Bash(lscpu:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 61: Bash(lsmem:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 62: Bash(ip:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 63: Bash(ifconfig:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 64: Bash(netstat:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 65: Bash(ss:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 66: Bash(ping:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 67: Bash(traceroute:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 68: Bash(nslookup:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 69: Bash(dig:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 70: Bash(host:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 71: Bash(curl:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 72: Bash(wget:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 73: Bash(nc:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 74: Bash(telnet:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 75: Bash(jq:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 76: Bash(yq:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 77: Bash(xargs:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 78: Bash(awk:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 79: Bash(sed:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 80: Bash(cut:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 81: Bash(tr:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 82: Bash(tee:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 83: Bash(xargs:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 84: Bash(read:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 85: Bash(printf:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 86: Bash(test:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 87: Bash([:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 88: Bash(true:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 89: Bash(false:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 90: Bash(exit:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 91: Bash(return:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 92: Bash(source:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 93: Bash(.:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 94: Bash(export:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 95: Bash(set:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 96: Bash(unset:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 97: Bash(alias:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 98: Bash(type:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 99: Bash(command:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 100: Bash(hash:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 101: Bash(time:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 102: Bash(timeout:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 103: Bash(watch:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 104: Bash(sleep:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 105: Bash(wait:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 106: Bash(kill:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 107: Bash(pkill:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 108: Bash(pgrep:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 109: Bash(jobs:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 110: Bash(fg:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 111: Bash(bg:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 112: Bash(nohup:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 113: Bash(disown:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 114: Bash(history:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 115: Bash(fc:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 116: Bash(pushd:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Operation requires approval - should be in 'ask'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 117: Bash(popd:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 118: Bash(dirs:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 119: Bash(tar:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 120: Bash(gzip:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 121: Bash(gunzip:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 122: Bash(zip:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 123: Bash(unzip:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 124: Bash(base64:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 125: Bash(md5sum:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 126: Bash(sha256sum:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 127: Bash(openssl:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 128: Bash(git status:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**
```bash
git status
```

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 129: Bash(git diff:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**
```bash
git diff
```
```bash
git diff HEAD~1
```

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 130: Bash(git log:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 131: Bash(git show:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 132: Bash(git remote:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 133: Bash(git fetch:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 134: Bash(git stash list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 135: Bash(git describe:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 136: Bash(git rev-parse:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 137: Bash(git config --get:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 138: Bash(git config --list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 139: Bash(git ls-files:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 140: Bash(git ls-tree:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 141: Bash(git cat-file:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 142: Bash(git blame:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 143: Bash(git shortlog:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 144: Bash(git reflog:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 145: Bash(git tag:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 146: Bash(git for-each-ref:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 147: Bash(aws sts:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 148: Bash(aws configure list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 149: Bash(aws configure get:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 150: Bash(aws ec2 describe-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 151: Bash(aws ec2 get-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 152: Bash(aws s3 ls:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 153: Bash(aws s3api get-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 154: Bash(aws s3api head-:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 155: Bash(aws s3api list-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 156: Bash(aws rds describe-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 157: Bash(aws iam get-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 158: Bash(aws iam list-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 159: Bash(aws lambda get-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 160: Bash(aws lambda list-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 161: Bash(aws logs describe-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 162: Bash(aws logs get-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 163: Bash(aws logs filter-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 164: Bash(aws cloudwatch describe-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 165: Bash(aws cloudwatch get-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 166: Bash(aws cloudwatch list-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 167: Bash(aws cloudformation describe-:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Operation requires approval - should be in 'ask'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 168: Bash(aws cloudformation get-:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Operation requires approval - should be in 'ask'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 169: Bash(aws cloudformation list-:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Operation requires approval - should be in 'ask'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 170: Bash(aws elbv2 describe-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 171: Bash(aws elb describe-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 172: Bash(aws route53 get-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 173: Bash(aws route53 list-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 174: Bash(aws secretsmanager get-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 175: Bash(aws secretsmanager list-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 176: Bash(aws secretsmanager describe-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 177: Bash(aws ssm get-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 178: Bash(aws ssm list-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 179: Bash(aws ssm describe-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 180: Bash(aws sns get-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 181: Bash(aws sns list-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 182: Bash(aws sqs get-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 183: Bash(aws sqs list-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 184: Bash(aws dynamodb describe-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 185: Bash(aws dynamodb list-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 186: Bash(aws dynamodb get-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 187: Bash(aws dynamodb scan:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 188: Bash(aws dynamodb query:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 189: Bash(aws ecr describe-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 190: Bash(aws ecr get-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 191: Bash(aws ecr list-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 192: Bash(aws eks describe-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 193: Bash(aws eks list-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 194: Bash(aws elasticache describe-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 195: Bash(gcloud version:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 196: Bash(gcloud info:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 197: Bash(gcloud auth:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 198: Bash(gcloud config:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 199: Bash(gcloud projects list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 200: Bash(gcloud projects describe:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 201: Bash(gcloud compute instances list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 202: Bash(gcloud compute instances describe:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 203: Bash(gcloud compute networks list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 204: Bash(gcloud compute networks describe:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 205: Bash(gcloud compute networks subnets list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 206: Bash(gcloud compute networks subnets describe:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 207: Bash(gcloud compute firewall-rules list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 208: Bash(gcloud compute firewall-rules describe:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 209: Bash(gcloud compute addresses list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 210: Bash(gcloud compute addresses describe:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 211: Bash(gcloud compute disks list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 212: Bash(gcloud compute disks describe:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 213: Bash(gcloud compute images list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 214: Bash(gcloud compute images describe:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 215: Bash(gcloud compute zones list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 216: Bash(gcloud compute regions list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 217: Bash(gcloud container clusters list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 218: Bash(gcloud container clusters describe:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 219: Bash(gcloud container clusters get-credentials:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 220: Bash(gcloud container node-pools list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 221: Bash(gcloud container node-pools describe:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 222: Bash(gcloud sql instances list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 223: Bash(gcloud sql instances describe:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 224: Bash(gcloud sql databases list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 225: Bash(gcloud sql users list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 226: Bash(gcloud redis instances list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 227: Bash(gcloud redis instances describe:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 228: Bash(gcloud iam service-accounts list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 229: Bash(gcloud iam service-accounts describe:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 230: Bash(gcloud iam service-accounts get-iam-policy:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 231: Bash(gcloud iam roles list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 232: Bash(gcloud iam roles describe:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 233: Bash(gcloud logging read:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 234: Bash(gcloud logging logs list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 235: Bash(gcloud services list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 236: Bash(gcloud artifacts repositories list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 237: Bash(gcloud artifacts docker images list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 238: Bash(gsutil ls:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 239: Bash(gsutil cat:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 240: Bash(gsutil stat:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 241: Bash(gsutil du:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 242: Bash(kubectl get:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**
```bash
kubectl get pods -n default
```
```bash
kubectl get services -A
```
```bash
kubectl get deployments
```

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 243: Bash(kubectl describe:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**
```bash
kubectl describe pod my-pod
```
```bash
kubectl describe service my-service
```

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 244: Bash(kubectl logs:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**
```bash
kubectl logs my-pod
```
```bash
kubectl logs my-pod -f
```

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 245: Bash(kubectl version:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 246: Bash(kubectl config:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 247: Bash(kubectl cluster-info:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 248: Bash(kubectl api-resources:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 249: Bash(kubectl api-versions:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 250: Bash(kubectl explain:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 251: Bash(kubectl top:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 252: Bash(kubectl auth:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 253: Bash(kubectl diff:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 254: Bash(kubectl wait:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 255: Bash(helm list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 256: Bash(helm status:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 257: Bash(helm get:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 258: Bash(helm template:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 259: Bash(helm version:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 260: Bash(helm repo list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 261: Bash(helm search:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 262: Bash(helm show:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 263: Bash(helm lint:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 264: Bash(helm history:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 265: Bash(helm env:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 266: Bash(flux get:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 267: Bash(flux check:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 268: Bash(flux version:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 269: Bash(flux logs:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 270: Bash(flux stats:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 271: Bash(flux tree:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 272: Bash(flux diff:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 273: Bash(flux events:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 274: Bash(terraform version:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Operation requires approval - should be in 'ask'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 275: Bash(terraform show:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Operation requires approval - should be in 'ask'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 276: Bash(terraform output:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Operation requires approval - should be in 'ask'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 277: Bash(terraform state list:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Operation requires approval - should be in 'ask'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 278: Bash(terraform state show:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Operation requires approval - should be in 'ask'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 279: Bash(terraform validate:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Operation requires approval - should be in 'ask'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 280: Bash(terraform fmt:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Operation requires approval - should be in 'ask'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 281: Bash(terraform providers:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Operation requires approval - should be in 'ask'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 282: Bash(terraform graph:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Operation requires approval - should be in 'ask'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 283: Bash(terraform console:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Operation requires approval - should be in 'ask'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 284: Bash(terragrunt version:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 285: Bash(terragrunt output:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 286: Bash(terragrunt validate:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 287: Bash(terragrunt graph-dependencies:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 288: Bash(terragrunt render-json:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 289: Bash(terragrunt hclfmt:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 290: Bash(docker ps:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 291: Bash(docker images:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 292: Bash(docker logs:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 293: Bash(docker inspect:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 294: Bash(docker version:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 295: Bash(docker info:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 296: Bash(docker stats:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 297: Bash(docker top:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 298: Bash(docker port:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 299: Bash(docker diff:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 300: Bash(docker history:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 301: Bash(docker network ls:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 302: Bash(docker network inspect:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 303: Bash(docker volume ls:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 304: Bash(docker volume inspect:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 305: Bash(docker compose ps:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 306: Bash(docker compose logs:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 307: Bash(docker compose config:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 308: Bash(python:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 309: Bash(python3:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 310: Bash(pip list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 311: Bash(pip show:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 312: Bash(pip freeze:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 313: Bash(pip check:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 314: Bash(node:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 315: Bash(npm list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 316: Bash(npm view:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 317: Bash(npm version:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 318: Bash(npm outdated:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 319: Bash(npm audit:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 320: Bash(npx:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 321: Bash(pnpm list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 322: Bash(yarn list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 323: Bash(go version:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 324: Bash(go list:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 325: Bash(go env:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not a clear read-only operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 326: Bash(rustc --version:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 327: Bash(cargo --version:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 328: Bash(java -version:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 329: Bash(javac -version:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 330: Bash(mvn --version:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Dangerous operation in 'allow' section - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 331: Bash(gradle --version:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it runs WITHOUT asking for approval
3. Verify it returns results successfully
4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---


## 2. DENY Rules - Should Block Automatically

These commands should be BLOCKED WITHOUT asking.

**Expected behavior:** Commands are blocked with an error message.

### Test 1: Bash(aws backup delete:*::*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 2: Bash(aws cloudformation delete-stack:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 3: Bash(aws dynamodb delete-table:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 4: Bash(aws dynamodb delete-item:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 5: Bash(aws ec2 delete-key-pair:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 6: Bash(aws ec2 delete-snapshot:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 7: Bash(aws ec2 delete-volume:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 8: Bash(aws ec2 terminate-instances:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not clearly a dangerous operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 9: Bash(aws elasticache delete-cache-cluster:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 10: Bash(aws elasticache delete-replication-group:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 11: Bash(aws iam delete-user:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 12: Bash(aws iam delete-role:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 13: Bash(aws iam delete-access-key:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 14: Bash(aws iam delete-group:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 15: Bash(aws iam delete-instance-profile:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 16: Bash(aws iam delete-policy:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 17: Bash(aws iam delete-role-policy:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 18: Bash(aws iam delete-user-policy:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 19: Bash(aws iam delete-group-policy:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 20: Bash(aws iam detach-user-policy:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not clearly a dangerous operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 21: Bash(aws iam detach-role-policy:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not clearly a dangerous operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 22: Bash(aws iam detach-group-policy:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not clearly a dangerous operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 23: Bash(aws iam remove-user-from-group:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not clearly a dangerous operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 24: Bash(aws lambda delete-function:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 25: Bash(aws rds delete-db-cluster-parameter-group:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 26: Bash(aws rds delete-db-cluster:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 27: Bash(aws rds delete-db-instance:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 28: Bash(aws rds delete-db-parameter-group:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 29: Bash(aws s3 rb:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not clearly a dangerous operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 30: Bash(aws s3api delete-bucket:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 31: Bash(aws s3api delete-objects:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 32: Bash(aws sns delete-topic:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Read-only operation in 'deny' section - should be in 'allow'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 33: Bash(aws sqs delete-queue:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 34: Bash(aws ec2 delete-security-group:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 35: Bash(aws ec2 delete-network-interface:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 36: Bash(aws ec2 delete-internet-gateway:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 37: Bash(aws ec2 delete-subnet:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 38: Bash(aws ec2 delete-vpc:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 39: Bash(aws ec2 delete-route:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 40: Bash(aws ec2 delete-route-table:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 41: Bash(dd:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not clearly a dangerous operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 42: Bash(fdisk:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not clearly a dangerous operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 43: Bash(gcloud compute firewall-rules delete:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 44: Bash(gcloud compute instances delete:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 45: Bash(gcloud compute networks delete:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 46: Bash(gcloud compute disks delete:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 47: Bash(gcloud compute images delete:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 48: Bash(gcloud compute snapshots delete:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 49: Bash(gcloud container clusters delete:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 50: Bash(gcloud iam roles delete:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 51: Bash(gcloud projects delete:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 52: Bash(gcloud services disable:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not clearly a dangerous operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 53: Bash(gcloud sql databases delete:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 54: Bash(gcloud sql instances delete:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 55: Bash(gcloud storage rm:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not clearly a dangerous operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 56: Bash(gsutil rb:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not clearly a dangerous operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 57: Bash(gsutil rm -r:*::*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not clearly a dangerous operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 58: Bash(kubectl delete cluster:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**
```bash
kubectl delete pod my-pod
```
```bash
kubectl delete deployment my-deployment
```

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 59: Bash(kubectl delete clusterrole:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**
```bash
kubectl delete pod my-pod
```
```bash
kubectl delete deployment my-deployment
```

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 60: Bash(kubectl delete clusterrolebinding:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**
```bash
kubectl delete pod my-pod
```
```bash
kubectl delete deployment my-deployment
```

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 61: Bash(kubectl delete namespace:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**
```bash
kubectl delete pod my-pod
```
```bash
kubectl delete deployment my-deployment
```

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 62: Bash(kubectl delete pv:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**
```bash
kubectl delete pod my-pod
```
```bash
kubectl delete deployment my-deployment
```

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 63: Bash(kubectl delete pvc:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**
```bash
kubectl delete pod my-pod
```
```bash
kubectl delete deployment my-deployment
```

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 64: Bash(kubectl delete persistentvolume:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**
```bash
kubectl delete pod my-pod
```
```bash
kubectl delete deployment my-deployment
```

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 65: Bash(kubectl delete persistentvolumeclaim:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**
```bash
kubectl delete pod my-pod
```
```bash
kubectl delete deployment my-deployment
```

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 66: Bash(kubectl delete node:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**
```bash
kubectl delete pod my-pod
```
```bash
kubectl delete deployment my-deployment
```

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 67: Bash(kubectl drain:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not clearly a dangerous operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 68: Bash(mkfs:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not clearly a dangerous operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 69: Bash(mkfs.ext4:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not clearly a dangerous operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 70: Bash(mkfs.ext3:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not clearly a dangerous operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 71: Bash(mkfs.fat:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not clearly a dangerous operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 72: Bash(mkfs.ntfs:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Not clearly a dangerous operation - validate pattern

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify it is BLOCKED immediately
3. Verify an error message is shown
4. Verify NO approval prompt is shown
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---


## 3. ASK Rules - Should Prompt for Approval

These commands should ASK for user approval before execution.

**Expected behavior:** User is prompted to approve/deny before execution.

### Test 1: Bash(terraform destroy:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**
```bash
terraform destroy
```
```bash
terraform destroy -auto-approve
```

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 2: Bash(terragrunt destroy:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 3: Bash(terraform apply:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 4: Bash(terragrunt apply:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 5: Bash(terraform plan:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 6: Bash(terragrunt plan:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 7: Bash(terraform init:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 8: Bash(terragrunt init:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 9: Bash(flux delete:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**
```bash
flux delete kustomization my-app
```

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 10: Bash(flux suspend:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 11: Bash(flux resume:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 12: Bash(flux reconcile:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 13: Bash(kubectl delete:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**
```bash
kubectl delete pod my-pod
```
```bash
kubectl delete deployment my-deployment
```

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 14: Bash(kubectl apply:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**
```bash
kubectl apply -f manifest.yaml
```
```bash
kubectl apply -k ./kustomize
```

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 15: Bash(kubectl create:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 16: Bash(kubectl patch:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 17: Bash(kubectl rollout:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 18: Bash(kubectl scale:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 19: Bash(kubectl set:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 20: Bash(kubectl exec:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 21: Bash(kubectl run:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 22: Bash(kubectl edit:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 23: Bash(kubectl label:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 24: Bash(kubectl annotate:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 25: Bash(kubectl taint:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 26: Bash(kubectl cordon:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 27: Bash(kubectl uncordon:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 28: Bash(helm delete:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 29: Bash(helm uninstall:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 30: Bash(helm install:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**
```bash
helm install my-release stable/nginx
```

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 31: Bash(helm upgrade:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**
```bash
helm upgrade my-release stable/nginx
```

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 32: Bash(helm rollback:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 33: Bash(helm repo add:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 34: Bash(helm repo update:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 35: Bash(helm repo remove:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 36: Bash(git push:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**
```bash
git push origin main
```

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 37: Bash(git pull:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 38: Bash(git rebase:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 39: Bash(git reset:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 40: Bash(git merge:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 41: Bash(git revert:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 42: Bash(git cherry-pick:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 43: Bash(git commit:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**
```bash
git commit -m "feat: add feature"
```

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 44: Bash(git add:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 45: Bash(git branch -d:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Read-only operation - should be in 'allow'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 46: Bash(git branch -D:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Read-only operation - should be in 'allow'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 47: Bash(git branch -m:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Read-only operation - should be in 'allow'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 48: Bash(git checkout -b:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Read-only operation - should be in 'allow'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 49: Bash(git switch -c:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 50: Bash(git stash:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 51: Bash(git stash drop:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 52: Bash(git stash pop:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 53: Bash(git stash apply:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 54: Bash(git clean:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 55: Bash(git restore:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 56: Bash(rm:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 57: Bash(rmdir:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 58: Bash(mv:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 59: Bash(cp:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 60: Bash(chmod:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 61: Bash(chown:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 62: Bash(mkdir:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 63: Bash(touch:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 64: Bash(ln:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 65: Bash(aws cloudformation create-stack:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 66: Bash(aws cloudformation update-stack:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 67: Bash(aws ec2 create-:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 68: Bash(aws ec2 modify-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 69: Bash(aws ec2 reboot-instances:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 70: Bash(aws ec2 run-instances:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 71: Bash(aws ec2 start-instances:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 72: Bash(aws ec2 stop-instances:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Read-only operation - should be in 'allow'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 73: Bash(aws iam attach-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 74: Bash(aws iam create-:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 75: Bash(aws iam put-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 76: Bash(aws iam update-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 77: Bash(aws lambda create-function:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 78: Bash(aws lambda update-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 79: Bash(aws rds create-:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 80: Bash(aws rds modify-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 81: Bash(aws rds start-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 82: Bash(aws rds stop-:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Read-only operation - should be in 'allow'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 83: Bash(aws s3 cp:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 84: Bash(aws s3 mv:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 85: Bash(aws s3 rm:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 86: Bash(aws s3 sync:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 87: Bash(aws s3api put-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 88: Bash(aws s3api create-:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 89: Bash(aws sns create-:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 90: Bash(aws sns publish:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 91: Bash(aws sqs create-:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 92: Bash(aws sqs send-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 93: Bash(aws secretsmanager create-:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 94: Bash(aws secretsmanager put-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 95: Bash(aws secretsmanager update-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 96: Bash(aws ssm put-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 97: Bash(aws ssm send-:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 98: Bash(gcloud compute instances create:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 99: Bash(gcloud compute instances reset:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 100: Bash(gcloud compute instances start:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 101: Bash(gcloud compute instances stop:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Read-only operation - should be in 'allow'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 102: Bash(gcloud compute disks create:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 103: Bash(gcloud compute networks create:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 104: Bash(gcloud compute networks subnets create:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 105: Bash(gcloud compute firewall-rules create:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 106: Bash(gcloud compute firewall-rules update:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 107: Bash(gcloud compute addresses create:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 108: Bash(gcloud container clusters create:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 109: Bash(gcloud container clusters update:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 110: Bash(gcloud container clusters resize:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 111: Bash(gcloud container node-pools create:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 112: Bash(gcloud container node-pools update:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 113: Bash(gcloud sql instances create:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 114: Bash(gcloud sql instances patch:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 115: Bash(gcloud sql instances restart:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 116: Bash(gcloud sql databases create:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 117: Bash(gcloud sql users create:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 118: Bash(gcloud sql users set-password:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 119: Bash(gcloud redis instances create:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 120: Bash(gcloud redis instances update:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 121: Bash(gcloud iam service-accounts create:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 122: Bash(gcloud iam service-accounts keys create:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 123: Bash(gcloud projects add-iam-policy-binding:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 124: Bash(gcloud functions deploy:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 125: Bash(gsutil cp:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 126: Bash(gsutil mv:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 127: Bash(gsutil rm:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 128: Bash(gsutil rsync:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 129: Bash(gsutil mb:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 130: Bash(docker build:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 131: Bash(docker push:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 132: Bash(docker pull:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 133: Bash(docker run:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 134: Bash(docker exec:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 135: Bash(docker stop:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Read-only operation - should be in 'allow'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 136: Bash(docker start:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 137: Bash(docker restart:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 138: Bash(docker rm:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 139: Bash(docker rmi:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 140: Bash(docker network create:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 141: Bash(docker network rm:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 142: Bash(docker volume create:*)
**Source:** `settings.json`
**Status:** ❌ Invalid
**Issue:** Too dangerous - should be in 'deny'

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 143: Bash(docker volume rm:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 144: Bash(docker compose up:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 145: Bash(docker compose down:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 146: Bash(docker compose build:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 147: Bash(npm install:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 148: Bash(npm ci:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 149: Bash(npm update:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 150: Bash(npm uninstall:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 151: Bash(npm publish:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 152: Bash(npm run:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 153: Bash(pnpm install:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 154: Bash(pnpm add:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 155: Bash(pnpm remove:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 156: Bash(yarn install:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 157: Bash(yarn add:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 158: Bash(yarn remove:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 159: Bash(pip install:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 160: Bash(pip uninstall:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 161: Bash(pip3 install:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---

### Test 162: Bash(pip3 uninstall:*)
**Source:** `settings.json`
**Status:** ✅ Valid

**Example commands:**

**Validation steps:**
1. Execute the example command
2. Verify an approval prompt is shown
3. Test DENY: Select 'No' and verify command is blocked
4. Test APPROVE: Select 'Yes' and verify command executes
5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail

---


## Validation Summary Checklist

- [ ] All 331 ALLOW rules execute automatically
- [ ] All 72 DENY rules block automatically
- [ ] All 162 ASK rules prompt for approval
- [ ] settings.json is strict (standard operations)
- [ ] settings.local.json is more open (query operations)