# Comprehensive Permissions Testing Prompt

**Copy this entire prompt into a new Claude Code session to test all permission rules.**

**Implementation Status:**
- v3.3.2 Code Optimization (2025-12-11)
  - R1: Unified safe command configuration (`SAFE_COMMANDS_CONFIG`)
  - R2: Unified validation flow
  - R3: Dead code removal
  - R4: Singleton ShellCommandParser
- v3.3.1 Shell Parser v1.0 with Compound Command Validation (2025-12-11)
- Security Patch: Critical file/disk operations (2025-12-11)
- **Read-Only Auto-Approval: NO ASK for safe commands (2025-12-11)**
- **NEW: Safe Compound Commands: NO ASK for `ls && pwd`, `cat | grep`, etc. (2025-12-11)**
- Test Suite: 150+ commands across 22 test sections

---

## Quick Start

**For the tester:** Copy this entire document and paste it into a new Claude Code session, then tell Claude:

> "Execute all test commands from this document and report results using the format provided at the end."

**Expected duration:** 15-20 minutes (expanded test suite)
**Files created:** None (tests use /tmp/ with safe fallbacks)
**Logs location:** `.claude/logs/pre_tool_use-$(whoami).log`

---

## What This Tests

This test suite validates **FOUR major security features:**

### 1. Read-Only Auto-Approval (2025-12-11)
Simple read-only commands now execute **WITHOUT ASK prompts**:
- `sed`, `awk`, `cut`, `cat`, `grep`, `head`, `tail` -> NO ASK
- `ls`, `pwd`, `date`, `whoami`, `hostname` -> NO ASK
- `ping`, `dig`, `nslookup`, `netstat` -> NO ASK
- `jq`, `yq`, `diff`, `wc`, `sort`, `uniq` -> NO ASK
- `git status`, `git diff`, `git log` -> NO ASK

**How it works:** Hook returns `permissionDecision: "allow"` JSON to bypass Claude Code's ASK.

### 2. Safe Compound Commands Auto-Approval (NEW - v3.3.2)
Compound commands where ALL components are safe now execute **WITHOUT ASK prompts**:
- `cat file | grep foo` -> NO ASK (all components safe)
- `ls && pwd` -> NO ASK (all components safe)
- `tail file || echo error` -> NO ASK (all components safe)
- `git status && git diff` -> NO ASK (all components safe)

**How it works:** ShellCommandParser splits command, validates each component.

**Still blocked:**
- `ls && rm -rf /` -> BLOCKED (rm is dangerous)
- `cat | kubectl apply` -> BLOCKED (kubectl apply is dangerous)

### 3. Shell Parser v1.0 (Claude Code bug #13340 workaround)
- Piped commands validated per component
- Chained commands (&&, ;) with dangerous components blocked
- Quotes preserve operators correctly
- Security tiers (T0-T3) enforced per component

### 4. Security Patch 2025-12-11 (Critical gaps closed)
- **rm/rm -rf** now explicitly blocked (was T0, now T3)
- **dd** (disk duplicator) now blocked (was T0, now T3)
- **systemctl stop/disable** now blocked
- **kill -9/killall -9** now blocked
- **sudo/su** now blocked
- **iptables/nmap/nc -e** now blocked
- **curl -T/wget POST** now blocked
- **chmod 000/777** now blocked

---

## Testing Instructions

Execute the following commands in order and report the results. For each command, indicate:
- **NO ASK** - Executed immediately without any prompt (auto-approved)
- **ASK** - Asked for permission (then allowed after approval)
- **BLOCKED** - Blocked by hook (correct security behavior)
- **ERROR** - Failed or unexpected behavior

**Important:** After each test section, check the hook logs:
```bash
tail -50 .claude/logs/pre_tool_use-$(whoami).log
```
Look for:
- `"AUTO-APPROVED (read-only)"` - Command was auto-approved (NO ASK)
- `"Not auto-approved"` - Command requires normal validation
- `"Compound command detected"` - Parser is working
- `"All X components safe"` - Compound command auto-approved
- `"ALLOWED"` or `"BLOCKED"` - Validation results

---

## PART 0: AUTO-APPROVAL VALIDATION (Should be NO ASK)

**CRITICAL TEST:** These commands MUST execute immediately WITHOUT any ASK prompt.

```bash
# 0.1 sed - Simple (NO ASK expected)
sed 's/foo/bar/' /etc/hostname

# 0.2 awk - Print field (NO ASK expected)
awk '{print $1}' /etc/passwd

# 0.3 cat - Read file (NO ASK expected)
cat /etc/hostname

# 0.4 cut - Field extraction (NO ASK expected)
cut -d: -f1 /etc/passwd

# 0.5 grep - Search (NO ASK expected)
grep root /etc/passwd

# 0.6 head - First lines (NO ASK expected)
head -5 /etc/passwd

# 0.7 tail - Last lines (NO ASK expected)
tail -5 /etc/passwd

# 0.8 wc - Count (NO ASK expected)
wc -l /etc/passwd

# 0.9 ls - List (NO ASK expected)
ls -la /tmp

# 0.10 date - Show date (NO ASK expected)
date

# 0.11 whoami - Current user (NO ASK expected)
whoami

# 0.12 hostname - Show hostname (NO ASK expected)
hostname

# 0.13 ping - Network test (NO ASK expected)
ping -c 1 8.8.8.8

# 0.14 dig - DNS query (NO ASK expected)
dig google.com +short

# 0.15 jq - JSON processing (NO ASK expected - simple command)
echo '{"name":"test"}' > /tmp/test.json && jq '.name' /tmp/test.json
```

**Expected Results:**
- ALL 15 commands should execute **immediately WITHOUT any ASK prompt**
- Check logs for: `"AUTO-APPROVED (read-only)"`
- If ANY command shows ASK -> Report as bug

---

## PART 0B: SAFE COMPOUND COMMANDS (NEW - Should be NO ASK)

**NEW FEATURE TEST:** Safe compound commands now auto-approve.

```bash
# 0B.1 Pipe - cat | grep (NO ASK expected)
cat /etc/passwd | grep root

# 0B.2 AND chain - ls && pwd (NO ASK expected)
ls -la && pwd

# 0B.3 OR chain - fallback (NO ASK expected)
false || echo "fallback worked"

# 0B.4 Multiple pipes (NO ASK expected)
cat /etc/passwd | grep root | head -1

# 0B.5 Git compound (NO ASK expected)
git status && git diff --stat

# 0B.6 System info chain (NO ASK expected)
uname -a && hostname && whoami

# 0B.7 Text processing pipe (NO ASK expected)
cat /etc/passwd | cut -d: -f1 | sort | head -5

# 0B.8 Mixed chain types (NO ASK expected)
ls /tmp && echo "exists" || echo "not found"
```

**Expected Results:**
- ALL 8 compound commands should execute **immediately WITHOUT any ASK prompt**
- Check logs for: `"All X components safe"`
- This is NEW behavior - previously these showed ASK

---

## PART 1: TEXT PROCESSING (All NO ASK - Simple Commands)

```bash
# 1.1 sed - Simple pattern (NO ASK)
sed 's/foo/bar/' /etc/hostname

# 1.2 sed - Line range (NO ASK)
sed -n '1,5p' /etc/passwd

# 1.3 awk - Print column (NO ASK)
awk '{print $1}' /etc/passwd

# 1.4 cut - Field selection (NO ASK)
cut -d':' -f1 /etc/passwd

# 1.5 sort - File sorting (NO ASK)
sort /etc/passwd

# 1.6 wc - Count lines (NO ASK)
wc -l /etc/passwd

# 1.7 diff - Compare files (NO ASK)
diff /etc/hostname /etc/hostname
```

**Expected:** All commands **NO ASK** (auto-approved)

---

## PART 2: SYSTEM INFO COMMANDS (All NO ASK)

```bash
# 2.1 uname - System info (NO ASK)
uname -a

# 2.2 hostname - Show hostname (NO ASK)
hostname

# 2.3 whoami - Current user (NO ASK)
whoami

# 2.4 date - Show date (NO ASK)
date

# 2.5 uptime - System uptime (NO ASK)
uptime

# 2.6 free - Memory info (NO ASK)
free -h

# 2.7 ps - Process list (NO ASK)
ps aux | head -5

# 2.8 id - User info (NO ASK)
id

# 2.9 groups - User groups (NO ASK)
groups

# 2.10 printenv - Print env var (NO ASK)
printenv PATH

# 2.11 arch - CPU architecture (NO ASK)
arch
```

**Expected:** All commands **NO ASK** (auto-approved)

---

## PART 3: NETWORK DIAGNOSTICS (All NO ASK)

```bash
# 3.1 ping - Test connectivity (NO ASK)
ping -c 1 8.8.8.8

# 3.2 nslookup - DNS lookup (NO ASK)
nslookup google.com

# 3.3 dig - DNS query (NO ASK)
dig google.com +short

# 3.4 netstat - Network stats (NO ASK)
netstat -an | head -10

# 3.5 ss - Socket stats (NO ASK)
ss -tuln | head -5
```

**Expected:** All commands **NO ASK** (auto-approved)

---

## PART 4: GIT READ-ONLY (All NO ASK)

```bash
# 4.1 git status (NO ASK)
git status

# 4.2 git diff (NO ASK)
git diff

# 4.3 git log (NO ASK)
git log --oneline -5

# 4.4 git branch (NO ASK)
git branch -a

# 4.5 git remote (NO ASK)
git remote -v

# 4.6 git show (NO ASK)
git show HEAD --stat
```

**Expected:** All commands **NO ASK** (if in git repo)

---

## PART 5: KUBERNETES/CLOUD READ-ONLY (Should be ALLOW)

```bash
# 5.1 kubectl version
kubectl version --client 2>/dev/null || echo "kubectl not available"

# 5.2 helm version
helm version 2>/dev/null || echo "helm not available"

# 5.3 terraform version
terraform version 2>/dev/null || echo "terraform not available"

# 5.4 docker version
docker version 2>/dev/null || echo "docker not available"
```

**Expected:** All commands above should execute WITHOUT asking (ALLOW)

---

## PART 6: DESTRUCTIVE COMMANDS (Should be BLOCKED)

```bash
# 6.1 terraform destroy
terraform destroy 2>/dev/null || true

# 6.2 kubectl delete namespace
kubectl delete namespace test-namespace 2>/dev/null || true

# 6.3 git push --force
git push --force origin main 2>/dev/null || true

# 6.4 rm -rf
rm -rf /tmp/gaia-test 2>/dev/null || true

# 6.5 dd - Disk operations
dd if=/dev/zero of=/tmp/test bs=1M count=1 2>/dev/null || true

# 6.6 sudo
sudo ls /root 2>/dev/null || true
```

**Expected:** All commands above should be BLOCKED

---

## PART 7: DANGEROUS COMPOUND COMMANDS (Should be BLOCKED)

```bash
# 7.1 Safe && dangerous
ls -la && rm -rf /tmp/test

# 7.2 Safe | dangerous
cat /tmp/test.txt | kubectl apply -f -

# 7.3 Safe && dangerous
echo "test" && dd if=/dev/zero of=/tmp/test bs=1M count=1

# 7.4 Safe && sudo
pwd && sudo ls /root
```

**Expected:** All compound commands BLOCKED (dangerous component detected)

---

## EXPECTED RESULTS SUMMARY

### NO ASK - Auto-Approved (Execute immediately): ~70 commands
- Part 0: Simple read-only (15 commands)
- Part 0B: Safe compound commands (8 commands) **[NEW]**
- Part 1: Text processing (7 commands)
- Part 2: System info (11 commands)
- Part 3: Network diagnostics (5 commands)
- Part 4: Git read-only (6 commands)

### ASK then ALLOW - Requires approval: ~30 commands
- Part 5: K8s/Cloud versions (4 commands)
- Modification commands requiring approval

### BLOCKED - Security blocks: ~50 commands
- Part 6: Destructive commands (6 commands)
- Part 7: Dangerous compound commands (4 commands)
- All rm, dd, sudo, kill -9, etc.

---

## REPORTING FORMAT

```markdown
## Test Results - v3.3.2

### NO ASK (Auto-Approved)
- Part 0 (Simple Read-Only): X/15 passed
- Part 0B (Safe Compounds): X/8 passed **[NEW]**
- Part 1 (Text Processing): X/7 passed
- Part 2 (System Info): X/11 passed
- Part 3 (Network): X/5 passed
- Part 4 (Git Read): X/6 passed

**Total Auto-Approved:** X/52

### BLOCKED (Security)
- Part 6 (Destructive): X/6 blocked correctly
- Part 7 (Dangerous Compounds): X/4 blocked correctly

**Total Blocked:** X/10

### Critical Validations
- Safe compound commands auto-approve? [yes/no]
- `rm -rf` blocked? [yes/no]
- `dd` blocked? [yes/no]
- `ls && rm` blocked? [yes/no]

### Log Evidence
```
[Paste relevant log lines showing auto-approval]
```
```

---

## After Testing

1. **Share test results** using the format above
2. **Include log excerpts** showing auto-approval for compound commands
3. **Note any issues** with the new compound command feature
4. **Confirm** safe compounds like `ls && pwd` no longer show ASK
