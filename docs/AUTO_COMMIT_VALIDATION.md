# Automatic Commit Message Validation

## Overview

Gaia-ops includes an automatic git commit-msg hook that validates and cleans commit messages before they are created. This system:

1. ✅ **Validates** commit format (Conventional Commits)
2. 🧹 **Cleans** Claude Code attribution footers automatically
3. 🔒 **Enforces** consistent commit standards across the project
4. 🚫 **Prevents** non-compliant commits from being created

## How It Works

### Automatic Installation

The commit-msg hook is installed automatically during:
- `npm install` (postinstall hook via `gaia-update.js`)
- `npm update` (postinstall hook via `gaia-update.js`)

### Commit Message Validation

When you run `git commit`:

```bash
git commit -m "feat(auth): add JWT token validation"
```

The hook executes and:

1. **Cleans** the message:
   - Removes `Generated with Claude Code` footer
   - Removes `Co-Authored-By: Claude` lines
   - Trims empty lines

2. **Validates** format:
   - Type: `feat|fix|docs|style|refactor|perf|test|build|ci|chore`
   - Scope: `(optional-scope):`
   - Description: `Clear description without period`
   - Max 72 chars for subject line

3. **Accepts or Rejects**:
   - ✅ Valid → Commit proceeds
   - ❌ Invalid → Shows error, prevents commit

## Format Examples

### ✅ Valid Commits
```
feat(auth): add JWT token validation
fix(api): resolve race condition in cache
docs(readme): update installation instructions
refactor(core): simplify error handling logic
chore(deps): update dependencies
```

### ❌ Invalid Commits
```
add JWT validation                           # Missing type
feat: Update authentication module.          # Period at end
feat(auth): add JWT token validation for users over 300 characters which exceeds max length
feat auth: missing parentheses              # Wrong format
```

## Preventing Claude Code Footers

When Claude Code generates commits with attribution:

```
feat(auth): add JWT validation

Generated with Claude Code
Co-Authored-By: Claude <noreply@anthropic.com>
```

The hook automatically **cleans** these lines before the commit is created. You don't need restrictions or special handling - the hook handles it transparently.

### Before Hook
```
feat(auth): add JWT validation

Generated with Claude Code
Co-Authored-By: Claude <noreply@anthropic.com>
```

### After Hook (What Gets Committed)
```
feat(auth): add JWT validation
```

## Commit History

After commits are processed by the hook:

```bash
git log --oneline

# Output
f0e7b03 feat(permissions): reorganize strategy
a1b2c3d fix(core): resolve initialization bug
d4e5f6g docs(api): update endpoint documentation
```

All commits are clean, consistent, and follow the project standard.

## Bypass (If Needed)

To bypass the commit-msg hook (not recommended):

```bash
git commit --no-verify -m "Your message here"
```

However, this is **not recommended** as it defeats the purpose of validation.

## Hook Installation Details

### Files Involved

1. **Hook Template**: `templates/hooks/commit-msg`
   - Python script for validation and cleaning
   - Distributed with the npm package

2. **Auto-Installer**: `bin/gaia-update.js`
   - Runs during postinstall
   - Copies hook to `.git/hooks/commit-msg`
   - Makes it executable (755 permissions)

3. **Installed Hook**: `.git/hooks/commit-msg`
   - Local copy in your repository
   - Runs before each commit
   - Custom to your project's .git

### Re-Installing Hooks

To re-install or update hooks:

```bash
npm install
# or
npm update
```

### Manual Installation

```bash
cp templates/hooks/commit-msg .git/hooks/commit-msg
chmod +x .git/hooks/commit-msg
```

## Configuration

Hook validation rules come from:
- `tools/4-validation/commit_validator.py`
- Configuration: `.claude/config/git_standards.json` (if using full validator)

## Troubleshooting

### Hook Not Running

1. Check if hook exists and is executable:
   ```bash
   ls -la .git/hooks/commit-msg
   ```
   Should show: `-rwxr-xr-x` (755)

2. Re-install hooks:
   ```bash
   npm install
   ```

3. Manually set execute permission:
   ```bash
   chmod +x .git/hooks/commit-msg
   ```

### "Commit message validation failed"

1. Check subject line (max 72 chars)
2. Ensure type is valid: `feat|fix|docs|style|refactor|perf|test|build|ci|chore`
3. No period at end of subject
4. Use conventional format: `type(scope): description`

### Cleaning Claude Footers Doesn't Work

The hook should auto-clean. If not:

1. Check if hook exists: `cat .git/hooks/commit-msg`
2. Check permissions: `ls -la .git/hooks/commit-msg`
3. Check Python version (requires 3.9+): `python3 --version`

## Development

### Hook Source Code

Location: `templates/hooks/commit-msg`

Main functions:
- `clean_commit_message()` - Removes Claude footers
- `validate_commit_format()` - Validates conventional commits
- `main()` - Orchestrates validation

### Testing Hook

```bash
# Create test commit message
echo "feat(test): test commit" > /tmp/test-msg.txt

# Run hook manually
python3 .git/hooks/commit-msg /tmp/test-msg.txt

# Check result
cat /tmp/test-msg.txt
```

### Modifying Hook

To customize validation:

1. Edit: `templates/hooks/commit-msg`
2. Re-install: `npm install`
3. Reinstall in existing repos: `npm update`

---

**Result**: Clean, consistent commit history without manual intervention.
