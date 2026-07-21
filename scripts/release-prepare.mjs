#!/usr/bin/env node
/**
 * release-prepare -- atomically bump every version source, rebuild dist/, and
 * validate, in one command. Invoked by the gaia-release skill's "release" flow
 * (step b), NOT run by hand: the whole point is that no human has to remember
 * the five files that must agree or the order they move in.
 *
 * The saga this prevents: bumping package.json but forgetting pyproject.toml
 * (which then ships stale and fails CI's validate-manifests leg AFTER the tag
 * is pushed -- a 5.0.3 -> 5.0.4 re-release). pre-publish:validate is the gate
 * that catches drift; this script makes drift impossible to introduce by hand
 * by writing all sources from a single target version, then running that same
 * gate locally so a drift fails here, loudly, before any tag exists.
 *
 * Steps (atomic -- a failure leaves the working tree for inspection, never
 * half-published):
 *   1. Bump ALL version sources to <version>:
 *        - package.json
 *        - pyproject.toml         ([project].version)
 *        - .claude-plugin/marketplace.json  (every plugin entry's top-level version)
 *        - CHANGELOG.md           (top versioned header; inserts a stub if absent)
 *      NOTE: .claude-plugin/plugin.json is NOT hand-bumped here -- it is a
 *      GENERATED artifact (metadata only, NO inline hooks; version inherited
 *      from package.json via the manifest's "from:package.json"). Step 2
 *      regenerates it.
 *   2. npm run generate:plugin-root  (regenerates the ROOT .claude-plugin/plugin.json
 *                                     (metadata only) + the canonical hooks/hooks.json
 *                                     from the manifest -- the source:npm plugin
 *                                     surface. Hooks live ONLY in hooks/hooks.json,
 *                                     never inline in plugin.json. No dist/ bundle
 *                                     exists anymore.)
 *   3. npm run pre-publish:validate  (the drift gate -- fails loud on any
 *                                     source that did not move)
 *
 * Idempotent: re-running with the same version is a no-op bump (sources already
 * agree) and re-validates. Usage:
 *   node scripts/release-prepare.mjs <version>
 *   npm run release:prepare <version>
 *
 * <version> is a bare semver, e.g. 5.0.5 or 5.1.0-rc.1 (no leading "v").
 */

import fs from 'fs';
import path from 'path';
import { execSync } from 'child_process';
import { fileURLToPath } from 'url';
import chalk from 'chalk';

const __filename = fileURLToPath(import.meta.url);
const REPO_ROOT = path.resolve(path.dirname(__filename), '..');

const SEMVER_RE = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/;

function log(msg, level = 'info') {
  const ts = new Date().toLocaleTimeString();
  const p = `[${ts}]`;
  switch (level) {
    case 'error': console.error(chalk.red(`${p} ✗ ${msg}`)); break;
    case 'success': console.log(chalk.green(`${p} ✓ ${msg}`)); break;
    case 'warning': console.warn(chalk.yellow(`${p} ⚠️  ${msg}`)); break;
    case 'step': console.log(chalk.bold.cyan(`\n${p} ${msg}`)); break;
    default: console.log(chalk.blue(`${p} ℹ️  ${msg}`));
  }
}

function fail(msg) {
  log(msg, 'error');
  process.exit(1);
}

function readText(rel) {
  return fs.readFileSync(path.join(REPO_ROOT, rel), 'utf-8');
}

function writeText(rel, content) {
  fs.writeFileSync(path.join(REPO_ROOT, rel), content);
}

function exists(rel) {
  return fs.existsSync(path.join(REPO_ROOT, rel));
}

// --- version-source bumpers ------------------------------------------------
// Each returns a short status string describing what it did, or throws.

function bumpJsonVersionField(rel, version) {
  const data = JSON.parse(readText(rel));
  const before = data.version;
  data.version = version;
  // Preserve npm's 2-space style + trailing newline (matches pre-publish-validate.js).
  writeText(rel, JSON.stringify(data, null, 2) + '\n');
  return `${rel}: ${before} -> ${version}`;
}

function bumpMarketplace(rel, version) {
  const data = JSON.parse(readText(rel));
  const plugins = data.plugins || [];
  if (plugins.length === 0) throw new Error(`${rel}: no plugins[] to bump`);
  const befores = plugins.map((p) => `${p.name}=${p.version}`);
  const tag = `v${version}`;
  const refUpdates = [];
  for (const plugin of plugins) {
    plugin.version = version;
    // Pin the source to the release tag for git/github sources so
    // `/plugin install` serves a fixed, reproducible tag instead of tracking
    // moving default-branch HEAD. Bumped atomically with the version so the
    // ref can never go stale (the earlier flagged follow-up). Guard: only
    // git/github object sources carry a ref -- npm/local sources must not.
    const src = plugin.source;
    if (src && typeof src === 'object' &&
        (src.source === 'github' || src.source === 'git')) {
      src.ref = tag;
      refUpdates.push(`${plugin.name}.source.ref=${tag}`);
    }
  }
  writeText(rel, JSON.stringify(data, null, 2) + '\n');
  const refNote = refUpdates.length ? ` (${refUpdates.join(', ')})` : '';
  return `${rel}: [${befores.join(', ')}] -> all ${version}${refNote}`;
}

function bumpPyproject(rel, version) {
  const text = readText(rel);
  // Bump only the version line inside the [project] table, not [tool.*] tables.
  const projectMatch = text.match(/(\[project\][\s\S]*?)(?=\n\[|$)/);
  if (!projectMatch) throw new Error(`${rel}: [project] section not found`);
  const block = projectMatch[1];
  const verLine = block.match(/^(\s*version\s*=\s*)["']([^"']+)["']/m);
  if (!verLine) throw new Error(`${rel}: [project].version not found`);
  const before = verLine[2];
  const newBlock = block.replace(
    /^(\s*version\s*=\s*)["'][^"']+["']/m,
    `$1"${version}"`,
  );
  writeText(rel, text.replace(block, newBlock));
  return `${rel}: ${before} -> ${version}`;
}

function bumpChangelog(rel, version) {
  const text = readText(rel);
  // Find the first real versioned header (skip "## [Unreleased]").
  const headerRe = /^##\s*\[([^\]]+)\](.*)$/gm;
  let m;
  while ((m = headerRe.exec(text)) !== null) {
    if (m[1].trim().toLowerCase() === 'unreleased') continue;
    if (m[1].trim() === version) {
      return `${rel}: top header already [${version}] (no change)`;
    }
    // Insert a new dated stub entry above the current top version, right after
    // the "## [Unreleased]" line if present, else above the first version header.
    const today = new Date().toISOString().slice(0, 10);
    const stub = `## [${version}] - ${today}\n\n`;
    const insertAt = m.index;
    const updated = text.slice(0, insertAt) + stub + text.slice(insertAt);
    writeText(rel, updated);
    return `${rel}: inserted stub [${version}] above [${m[1].trim()}] ` +
      `(EDIT the body before release)`;
  }
  throw new Error(`${rel}: no versioned header found to anchor the new entry`);
}

// --- main ------------------------------------------------------------------

function run(cmd) {
  log(`Running: ${cmd}`, 'info');
  execSync(cmd, { cwd: REPO_ROOT, stdio: 'inherit' });
}

function main() {
  const version = process.argv[2];
  if (!version) {
    fail('Usage: node scripts/release-prepare.mjs <version>  (e.g. 5.0.5 or 5.1.0-rc.1)');
  }
  if (version.startsWith('v')) {
    fail(`Pass a bare semver without the leading "v" (got "${version}"). The tag adds the v; the sources do not carry it.`);
  }
  if (!SEMVER_RE.test(version)) {
    fail(`"${version}" is not a valid semver. Expected MAJOR.MINOR.PATCH with optional -prerelease.`);
  }

  log(`Target version: ${version}`, 'step');

  // Step 1 -- atomic bump of every version source.
  log('Step 1: Bumping all version sources atomically...', 'step');
  const results = [];
  try {
    results.push(bumpJsonVersionField('package.json', version));
    results.push(bumpPyproject('pyproject.toml', version));
    // .claude-plugin/plugin.json is intentionally NOT bumped here -- it is
    // regenerated in Step 2 (generate:plugin-root), inheriting the version from
    // package.json via the manifest's "from:package.json".
    if (exists('.claude-plugin/marketplace.json')) {
      results.push(bumpMarketplace('.claude-plugin/marketplace.json', version));
    }
    results.push(bumpChangelog('CHANGELOG.md', version));
  } catch (err) {
    fail(`Version bump failed (working tree left for inspection): ${err.message}`);
  }
  for (const r of results) log(`  ${r}`, 'success');

  // Step 2 -- regenerate the ROOT plugin manifests (plugin.json is metadata
  // only; hooks live ONLY in the canonical hooks/hooks.json, never inline)
  // so the source:npm plugin surface carries the new version. No dist/ bundle.
  log('Step 2: Regenerating root plugin manifests (npm run generate:plugin-root)...', 'step');
  try {
    run('npm run generate:plugin-root');
  } catch {
    fail('generate:plugin-root failed -- root .claude-plugin/plugin.json / hooks/hooks.json not regenerated. Fix the generator, then re-run release:prepare.');
  }
  log('root manifests regenerated', 'success');

  // Step 3 -- the drift gate. Fails loud if any source did not move.
  log('Step 3: Validating version sync (npm run pre-publish:validate)...', 'step');
  try {
    run('npm run pre-publish:validate');
  } catch {
    fail('pre-publish:validate FAILED -- version drift or a manifest problem remains. ' +
      'This is the gate that protects the release; do NOT tag until it is green.');
  }

  log(`release:prepare complete -- all sources at ${version}, root manifests regenerated, validation green.`, 'success');
  log('Next (driven by the gaia-release "release" flow, not by hand): pre-flight (Python 3.11/3.12 + tests), commit, tag, push, gh release.', 'info');
}

main();
