import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import {
  mkdirSync,
  mkdtempSync,
  writeFileSync,
  symlinkSync,
  readlinkSync,
  existsSync,
  rmSync,
} from 'node:fs';
import { tmpdir, platform } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const SCRIPT_PATH = join(REPO_ROOT, 'bin', 'gaia-update.js');

/**
 * Build a minimal consumer project tree at a tmp dir:
 *
 *   tmp/
 *     node_modules/@jaguilar87/<pkgName>/...     (the fake installed package)
 *     .claude/                                    (the consumer's .claude dir)
 *
 * Returns the absolute paths of tmp/, claude/, and the fake package.
 */
function setupConsumer({ pkgName = 'gaia', extraSymlinks = {} } = {}) {
  const root = mkdtempSync(join(tmpdir(), 'gaia-update-test-'));
  const claudeDir = join(root, '.claude');
  const pkgDir = join(root, 'node_modules', '@jaguilar87', pkgName);

  mkdirSync(claudeDir, { recursive: true });
  mkdirSync(pkgDir, { recursive: true });

  // Create the source dirs that should be linked into .claude/
  for (const name of ['agents', 'tools', 'hooks', 'commands', 'templates', 'config', 'skills']) {
    mkdirSync(join(pkgDir, name), { recursive: true });
  }
  writeFileSync(join(pkgDir, 'CHANGELOG.md'), '# Changelog\n');

  // package.json for version detection
  writeFileSync(
    join(pkgDir, 'package.json'),
    JSON.stringify({ name: `@jaguilar87/${pkgName}`, version: '5.0.0-test' }, null, 2),
  );

  // Apply extra symlinks requested by the test (e.g. broken or legacy ones)
  for (const [linkName, target] of Object.entries(extraSymlinks)) {
    const linkPath = join(claudeDir, linkName);
    try {
      symlinkSync(target, linkPath, platform() === 'win32' ? 'junction' : 'dir');
    } catch { /* best effort in test env */ }
  }

  return { root, claudeDir, pkgDir };
}

function runGaiaUpdate(root) {
  return spawnSync(process.execPath, [SCRIPT_PATH], {
    env: { ...process.env, INIT_CWD: root, GAIA_SKIP_FTS5_BACKFILL: '1' },
    cwd: root,
    encoding: 'utf-8',
    timeout: 30_000,
  });
}


test('resolveGaiaPackageName returns canonical name when @jaguilar87/gaia is installed', async () => {
  const { resolveGaiaPackageName } = await import(pathToFileURL(SCRIPT_PATH).href);
  const { root } = setupConsumer({ pkgName: 'gaia' });
  try {
    assert.equal(resolveGaiaPackageName(root), 'gaia');
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('resolveGaiaPackageName falls back to legacy gaia-ops when only that is present', async () => {
  const { resolveGaiaPackageName } = await import(pathToFileURL(SCRIPT_PATH).href);
  const { root } = setupConsumer({ pkgName: 'gaia-ops' });
  try {
    assert.equal(resolveGaiaPackageName(root), 'gaia-ops');
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('resolveGaiaPackageName returns null when @jaguilar87 scope is missing', async () => {
  const { resolveGaiaPackageName } = await import(pathToFileURL(SCRIPT_PATH).href);
  const root = mkdtempSync(join(tmpdir(), 'gaia-update-test-noscope-'));
  try {
    assert.equal(resolveGaiaPackageName(root), null);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

// Symlink repair tests are Unix-only; on Windows junctions behave differently
// and the CI we publish to runs on Linux.
if (platform() !== 'win32') {
  test('postinstall repairs symlink whose target no longer exists', () => {
    // Pre-create a broken symlink in .claude/ that points nowhere.
    const { root, claudeDir, pkgDir } = setupConsumer({
      pkgName: 'gaia',
      extraSymlinks: {
        // Points at a path that does not exist on disk — must be repaired.
        agents: '/tmp/this-path-does-not-exist-for-gaia-update-test',
      },
    });
    try {
      const result = runGaiaUpdate(root);
      // Script never exits non-zero by design (never fails npm install)
      assert.equal(result.status, 0, `stderr: ${result.stderr}`);

      const linkPath = join(claudeDir, 'agents');
      assert.ok(existsSync(linkPath), 'agents symlink should exist after repair');
      const raw = readlinkSync(linkPath);
      const absTarget = raw.startsWith('/') ? raw : resolve(claudeDir, raw);
      assert.equal(absTarget, join(pkgDir, 'agents'), 'symlink should point at the installed package');
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  test('postinstall repairs legacy gaia-ops symlink after package rename', () => {
    // Simulate an upgrade path: package on disk is `gaia`, but a stale
    // symlink still points at the old `@jaguilar87/gaia-ops` path (which we
    // materialize as a real dir so existsSync returns true — forcing the
    // repair to run via the legacy-name branch rather than the missing-target
    // branch).
    const root = mkdtempSync(join(tmpdir(), 'gaia-update-test-legacy-'));
    const claudeDir = join(root, '.claude');
    const newPkg = join(root, 'node_modules', '@jaguilar87', 'gaia');
    const legacyPkg = join(root, 'node_modules', '@jaguilar87', 'gaia-ops');
    mkdirSync(claudeDir, { recursive: true });
    mkdirSync(newPkg, { recursive: true });
    mkdirSync(legacyPkg, { recursive: true });
    for (const name of ['agents', 'tools', 'hooks', 'commands', 'templates', 'config', 'skills']) {
      mkdirSync(join(newPkg, name), { recursive: true });
      mkdirSync(join(legacyPkg, name), { recursive: true });
    }
    writeFileSync(join(newPkg, 'CHANGELOG.md'), '# New\n');
    writeFileSync(join(legacyPkg, 'CHANGELOG.md'), '# Legacy\n');
    writeFileSync(
      join(newPkg, 'package.json'),
      JSON.stringify({ name: '@jaguilar87/gaia', version: '5.0.0' }),
    );

    // Legacy symlink — existing target but wrong package name
    symlinkSync(join(legacyPkg, 'agents'), join(claudeDir, 'agents'), 'dir');

    try {
      const result = runGaiaUpdate(root);
      assert.equal(result.status, 0, `stderr: ${result.stderr}`);

      const raw = readlinkSync(join(claudeDir, 'agents'));
      // The repaired symlink must point at the current `gaia` package, NOT
      // the legacy `gaia-ops` path.
      assert.ok(
        !raw.includes('gaia-ops'),
        `symlink should no longer reference gaia-ops, got: ${raw}`,
      );
      assert.ok(
        raw.includes('@jaguilar87/gaia'),
        `symlink should reference @jaguilar87/gaia, got: ${raw}`,
      );
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
}
