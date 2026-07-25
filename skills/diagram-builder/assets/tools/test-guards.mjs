// test-guards.mjs — permanent NEGATIVE-test suite for the diagram-builder
// guards (check-layout.mjs, build-data.mjs strict-schema, validate-layout.cjs
// invariant A). `check` is now the mandatory gate for every deck edit; this
// suite exists to prove the gate actually DETECTS what it claims to, not just
// that it runs. Each case fabricates one broken deck in os.tmpdir() (never
// inside the repo), runs the real guard against it, and asserts the guard
// FAILS with the expected message. A guard that goes quiet on a real defect
// is a silent false negative — the failure mode this suite exists to catch.
//
// Run: npm test  (or: node tools/test-guards.mjs)
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import yaml from 'js-yaml';

const require = createRequire(import.meta.url);
const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const CHECK = path.join(ROOT, 'tools', 'check-layout.mjs');
const BUILD = path.join(ROOT, 'engine', 'build-data.mjs');

let failures = 0;
function report(name, ok, detail) {
  console.log(`[${ok ? 'PASS' : 'FAIL'}] ${name}${!ok && detail ? ' — ' + detail : ''}`);
  if (!ok) failures++;
}

// A minimal deck fixture: just data/document.yaml + data/pages/*.yaml, which
// is all loadAuthoredDeck() reads. data.generated.js is deliberately NOT
// copied — its absence only fails the unrelated CENSUS line, never RECT/CHIP.
function mkDeck() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'diagram-guard-'));
  fs.mkdirSync(path.join(dir, 'data'));
  fs.copyFileSync(path.join(ROOT, 'data', 'document.yaml'), path.join(dir, 'data', 'document.yaml'));
  fs.cpSync(path.join(ROOT, 'data', 'pages'), path.join(dir, 'data', 'pages'), { recursive: true });
  return dir;
}
function loadOverview(dir) {
  const p = path.join(dir, 'data', 'pages', 'overview.yaml');
  return { p, doc: yaml.load(fs.readFileSync(p, 'utf8')) };
}
function saveOverview(p, doc) { fs.writeFileSync(p, yaml.dump(doc), 'utf8'); }
function findNode(doc, id) {
  const walk = list => { for (const n of list || []) { if (n.id === id) return n;
    if (Array.isArray(n.children)) { const r = walk(n.children); if (r) return r; } } return null; };
  return walk(doc.sections);
}
function runNode(args) {
  const opts = { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] };
  try { return { code: 0, out: execFileSync('node', args, opts) }; }
  catch (e) { return { code: e.status ?? 1, out: (e.stdout || '') + (e.stderr || '') }; }
}
// fs.rmSync here is a Node API call inside THIS process, not a chained shell
// `rm` — the T3 gate only classifies Bash-tool commands, so cleanup is not
// expected to be blocked; the try/catch is defensive against OS-level errors.
function rmDeck(dir) { try { fs.rmSync(dir, { recursive: true, force: true }); } catch { /* best-effort */ } }

// ── 1. RECT — section-e short by exactly 1 cell after removing item-b ─────
// NOT section-a/item-2 (see cross_layer_impacts): a 2-col, 2-child section
// reduced to 1 child is absorbed by the documented grow-with-content clamp
// (effectiveCols shrinks to 1, so 1 cell in 1 track still closes). section-e
// (4 authored cols, span 1+1+2) already clamps to effectiveCols=2 with all
// 3 children present; dropping the span-1 "item-b" leaves span 1+2=3 that
// cannot fit 2-per-row, forcing a genuine 2×2=4 rectangle with a real hole.
{
  const dir = mkDeck();
  const { p, doc } = loadOverview(dir);
  const se = findNode(doc, 'section-e');
  se.children = se.children.filter(c => c.id !== 'item-b');
  saveOverview(p, doc);
  const { code, out } = runNode([CHECK, dir]);
  const ok = code !== 0 && out.includes('SHORT BY EXACTLY 1 cell(s)');
  report('RECT: section-e short by 1 cell', ok, `exit=${code}`);
  rmDeck(dir);
}

// ── 2a. Invariant A, layer 1 — build-data.mjs rejects unknown page form ────
{
  const dir = mkDeck();
  fs.mkdirSync(path.join(dir, 'engine'));
  fs.copyFileSync(BUILD, path.join(dir, 'engine', 'build-data.mjs'));
  fs.symlinkSync(path.join(ROOT, 'node_modules'), path.join(dir, 'node_modules'), 'dir');
  const { p, doc } = loadOverview(dir);
  doc.form = 'dashboards';
  saveOverview(p, doc);
  const { code, out } = runNode([path.join(dir, 'engine', 'build-data.mjs')]);
  const ok = code !== 0 && out.includes('[strict-schema]') && out.includes('unknown page form "dashboards"');
  report('A/build-data: unknown page form "dashboards"', ok, `exit=${code}`);
  rmDeck(dir);
}

// ── 2b. Invariant A, layer 2 — runInvariants({}, {form:'dashboards'}) ──────
{
  const { runInvariants } = require(path.join(ROOT, 'tools', 'validate-layout.cjs'));
  const checks = runInvariants({}, { form: 'dashboards' });
  const ok = checks.length === 1 && checks[0].id === 'A' && checks[0].ok === false;
  report('A/runInvariants: single check A, ok:false', ok, JSON.stringify(checks));
}

// ── 3. CHIP — orphan chip, dangling key, arity-1 — one fixture, one run ────
{
  const dir = mkDeck();
  const { p, doc } = loadOverview(dir);
  doc.filters.push({ key: 'ghost-chip', label: 'Ghost' });      // 0 members -> orphan
  findNode(doc, 'item-2').filters = ['dangling-key'];            // referenced, undeclared
  delete findNode(doc, 'item-3').filters;                        // "flow" left with...
  delete findNode(doc, 'item-7').filters;                        // ...only item-1 -> arity 1
  saveOverview(p, doc);
  const { code, out } = runNode([CHECK, dir]);
  const ok = code !== 0
    && out.includes('chip "ghost-chip"') && out.includes('NO component references it')
    && out.includes('key "dangling-key"') && out.includes('NO chip declares it')
    && out.includes('chip "flow"') && out.includes('has exactly ONE member');
  report('CHIP: orphan + dangling key + arity-1', ok, `exit=${code}`);
  rmDeck(dir);
}

// ── 4. control positive — the intact seed must pass ────────────────────────
{
  const { code, out } = runNode([CHECK, ROOT]);
  const ok = code === 0 && out.includes('ALL PASS');
  report('control: intact seed passes', ok, `exit=${code}`);
}

console.log(`\n${failures === 0 ? 'OK' : 'FAILED'} — ${failures} guard(s) did not detect their defect.`);
process.exit(failures === 0 ? 0 : 1);
