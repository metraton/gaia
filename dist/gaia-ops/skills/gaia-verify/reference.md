# Gaia Verify Reference

Exact commands for each mode. Copy and run -- no interpretation needed.

## Mode: live

```bash
gaia-doctor
gaia-status
```

No temp directory. No cleanup.

## Mode: dry-run

1. Go to the source repo (renamed to `gaia-dev` post-rename):
   `cd /home/jorge/ws/me/gaia-dev`

2. Pack the package (from the gaia source repo; path below uses the future rename but current dir is still `gaia-ops-dev`):
   `npm pack`
   Note the `.tgz` filename output (e.g., `jaguilar87-gaia-5.3.0.tgz`).

3. Create a clean temp project (use actual timestamp):
   `mkdir /tmp/gaia-dry-run-$(date +%Y%m%d%H%M%S)`

4. Initialize:
   `npm init -y` (run inside the temp dir)

5. Install from tarball (use absolute path):
   `npm install /home/jorge/ws/me/gaia-dev/jaguilar87-gaia-X.Y.Z.tgz`

6. Verify:
   `npx gaia-doctor`
   `npx gaia-status`

7. Clean up:
   `rm -rf /tmp/gaia-dry-run-*`

## Mode: beta

1. Create a clean temp project (use actual timestamp):
   `mkdir /tmp/gaia-beta-verify-$(date +%Y%m%d%H%M%S)`

2. Initialize:
   `npm init -y` (run inside the temp dir)

3. Install from npm registry:
   `npm install @jaguilar87/gaia@beta`

4. Verify:
   `npx gaia-doctor`
   `npx gaia-status`

5. Clean up:
   `rm -rf /tmp/gaia-beta-verify-*`

## Mode: release

1. Create a clean temp project (use actual timestamp):
   `mkdir /tmp/gaia-release-verify-$(date +%Y%m%d%H%M%S)`

2. Initialize:
   `npm init -y` (run inside the temp dir)

3. Install from npm registry:
   `npm install @jaguilar87/gaia@latest`

4. Verify:
   `npx gaia-doctor`
   `npx gaia-status`

5. Clean up:
   `rm -rf /tmp/gaia-release-verify-*`

## Notes

- Run each command separately and verify exit code before proceeding (command-execution discipline).
- For dry-run, `npm pack` must be run from `gaia-dev` -- the `.tgz` lands in the current working directory.
- For beta/release, the install step requires network access to the npm registry. If it fails with `E404`, the version has not published yet -- wait and retry.
- `npx gaia-doctor` exits non-zero on failure. If it fails, stop and report the error. Do not run `gaia-status`.
