---
name: gaia-devmode
description: Use when switching Gaia between dev and release modes, testing changes locally, or preparing a release
metadata:
  user-invocable: false
  type: technique
---

# Gaia Dev Mode

How to test Gaia changes locally and release them. The user says
"test this here" or "release it" — you figure out the fastest path.

## Step 1: Detect Current Mode

Check where `.claude/hooks` points:

| Target contains | Mode | Meaning |
|-----------------|------|---------|
| `gaia-ops-dev/` | Dev | Editing source directly, changes are live |
| `node_modules/` | Release | Running published package |

If symlinks don't exist, Gaia may not be installed yet.

## Step 2: Switch to Dev Mode

Goal: make `.claude/` point to the gaia-ops-dev working tree.

**Fastest path — symlinks (when both repos are on same machine):**
Create symlinks for: agents, hooks, skills, config, tools, commands.
Each points from `.claude/<dir>` to `gaia-ops-dev/<dir>`.

**Alternative — npm install from local (when testing install flow):**
`npm install /path/to/gaia-ops-dev` in the target project.
This runs postinstall (gaia-update.js) which creates symlinks to node_modules.

After switching: `npx gaia-doctor` to verify health.
User must restart Claude Code for changes to take effect.

## Step 3: Switch to Release Mode

Goal: revert to the published npm package.

`npm install @jaguilar87/gaia-ops` in the target project.
Postinstall recreates symlinks to `node_modules/@jaguilar87/gaia-ops/`.

## Step 4: Test in Isolation

When you need a clean environment without touching any real project:

1. Create temp project: `mkdir /tmp/gaia-test-YYYYMMDD && cd` into it
2. `npm init -y`
3. `npm install /path/to/gaia-ops-dev` (from local source)
4. `npx gaia-doctor`
5. Test what you need
6. Delete when done — `/tmp/` is ephemeral

## Step 5: Release

Only after all tests pass:

1. Version bump: `npm version patch|minor|major`
2. Build both plugins: `npm run build:plugins`
3. Validate: `npm run pre-publish:validate`
4. Publish: `npm publish`
5. Verify: install in a test project from npm registry

See gaia-patterns for version increment criteria (patch/minor/major).

## Decision Tree

```
User says "test this here"
├── Same machine as gaia-ops-dev? → symlinks (fastest)
├── Need to test install flow? → npm install from local
└── Need clean environment? → /tmp/ isolation project

User says "release it"
→ Step 5 (tests → version → build → validate → publish)

User says "go back to release"
→ npm install @jaguilar87/gaia-ops
```

## Anti-Patterns

- Editing files in `node_modules/` instead of switching to dev mode
- Testing only in dev mode and assuming release works the same
- Publishing without `npm run build:plugins` (dist/ will be stale)
- Forgetting to tell the user to restart Claude Code after switching
