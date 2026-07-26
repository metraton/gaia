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
import { widthAtTier, isBandAtTier, isBandClass, place,
  textBudget, capacityFor, MONO_ADVANCE_EM } from './check-layout.mjs';

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
// NOT a section whose child count is what pins its track count: dropping a cell
// there is absorbed by the documented grow-with-content clamp (effectiveCols
// shrinks with the content, so the smaller rectangle still closes) and the guard
// legitimately stays quiet. section-e is the right fixture because its 4 tracks
// SURVIVE the removal: it authors columns:4 with six span-1 cells + one span-2
// merge (area 6×1+1×2 = 8 = 4×2, closed), so with "item-b" gone the clamp still
// sees 5 single cells and keeps 4 tracks — leaving area 7 against a 4×2=8
// rectangle, i.e. a real hole of exactly one cell in the last row.
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

// ── 5. PLACEMENT AGREEMENT — the engine's model vs the gate's mirror ───────
// engine.js and check-layout.mjs each implement the same CSS-grid placement, and
// they must: the engine is a plain browser script under `file://`, where an ES
// module is CORS-blocked (origin 'null'), so there is no module system to share
// one through and the deck's contract is that it opens with a double click.
// What CAN be shared is the PROOF. These cases extract the engine's own
// functions from its real source and assert they agree with the gate's copy over
// a corpus of grid shapes — so "keep in sync" is a test, not a comment.
const ENGINE_SRC = fs.readFileSync(path.join(ROOT, 'engine', 'engine.js'), 'utf8');

// Lift named functions out of the engine by brace matching from their
// declarations, evaluated together so they can call each other. Reads the REAL
// source, so an engine edit either still agrees or is caught; a rename makes the
// extraction throw, which fails the case rather than skipping it.
function liftFromEngine(...names) {
  const decls = names.map(name => {
    const at = ENGINE_SRC.indexOf(`function ${name}(`);
    if (at < 0) throw new Error(`engine.js declares no \`function ${name}(\` — the agreement test cannot see it`);
    let depth = 0, end = -1;
    for (let i = ENGINE_SRC.indexOf('{', at); i < ENGINE_SRC.length; i++) {
      if (ENGINE_SRC[i] === '{') depth++;
      else if (ENGINE_SRC[i] === '}' && --depth === 0) { end = i + 1; break; }
    }
    if (end < 0) throw new Error(`\`function ${name}\` in engine.js has unbalanced braces`);
    return ENGINE_SRC.slice(at, end);
  });
  return new Function(`${decls.join('\n')}\nreturn { ${names.join(', ')} };`)();
}

// Every (span, cols, tracks) the cascade can reach: tracks is the authored count,
// the 2-track intermediate, or the 1-track endpoint, and never widens the grid.
function widthCorpus() {
  const out = [];
  for (let cols = 1; cols <= 6; cols++)
    for (let span = 1; span <= cols; span++)
      for (const tracks of [cols, 2, 1])
        if (tracks <= cols) out.push({ span, cols, tracks });
  return out;
}

// The grid shapes: every ordering of up to four slots drawn from a set that mixes
// single cells, partial merges, bands and rowspans — enough for a merge to fail to
// fit and drop a row, which is where two placement models drift apart.
function shapeCorpus() {
  const spans = [1, 2, 3, 4];
  const rowspans = [1, 2];
  const slots = [];
  for (const span of spans) for (const rowspan of rowspans) slots.push({ span, rowspan });
  const shapes = [];
  for (let cols = 1; cols <= 5; cols++) {
    for (const a of slots) for (const b of slots) for (const c of slots) {
      const trio = [a, b, c].filter(s => s.span <= cols);
      if (trio.length) shapes.push({ cols, slots: trio });
    }
  }
  return shapes;
}

{
  let mismatch = null;
  try {
    const { widthAtTier: engineWidth } = liftFromEngine('widthAtTier');
    for (const { span, cols, tracks } of widthCorpus()) {
      const mine = widthAtTier(span, cols, tracks), theirs = engineWidth(span, cols, tracks);
      if (mine !== theirs) { mismatch = `span ${span} of ${cols} at ${tracks} track(s): gate ${mine} vs engine ${theirs}`; break; }
    }
  } catch (e) { mismatch = e.message; }
  report('AGREE/width: gate widthAtTier == engine widthAtTier', mismatch === null, mismatch);
}

{
  let mismatch = null;
  try {
    const { widthAtTier: engineWidth, rowOccupants: engineRows } =
      liftFromEngine('widthAtTier', 'isBandAtTier', 'rowOccupants');
    for (const shape of shapeCorpus()) {
      for (const tracks of [shape.cols, 2, 1]) {
        if (tracks > shape.cols) continue;
        const items = shape.slots.map((s, i) => ({
          node: `s${i}`, id: `s${i}`,
          w: engineWidth(Math.min(s.span, shape.cols), shape.cols, tracks), h: s.rowspan,
        }));
        // The engine returns occupants PER ROW; the gate returns coordinates. Both
        // answer the same question — which row each slot lands on — so compare that.
        const engineByRow = engineRows(items, tracks)
          .map((occ, r) => (occ || []).map(n => `${n}@${r}`)).flat().sort();
        const gateByRow = place(items, tracks).placed
          .map(p => Array.from({ length: p.h }, (_, i) => `${p.id}@${p.r + i}`)).flat().sort();
        if (engineByRow.join('|') !== gateByRow.join('|')) {
          mismatch = `cols ${shape.cols} @${tracks} tracks, spans [${shape.slots.map(s => `${s.span}x${s.rowspan}`).join(' ')}]: ` +
            `engine [${engineByRow.join(' ')}] vs gate [${gateByRow.join(' ')}]`;
          break;
        }
      }
      if (mismatch) break;
    }
  } catch (e) { mismatch = e.message; }
  report('AGREE/placement: gate place == engine rowOccupants over the shape corpus', mismatch === null, mismatch);
}

// The band rule is where the two ACTUALLY diverged: the engine asks `w >= tracks`
// (tier-relative, which is what the CSS does — at the 640px endpoint a .mspan
// becomes grid-column:1/-1) while the gate asked a precomputed `span >= cols`.
// A span-3-of-4 at the 2-track tier is the case that split them. This asserts the
// gate now answers it the engine's way, and that the .msp CLASS question — the one
// the root's `:has(> .msp)` rule keys on — still gets the authored answer.
{
  const w = widthAtTier(3, 4, 2);
  const ok = w === 2 && isBandAtTier(w, 2) === true && isBandClass(3, 4) === false
    && isBandAtTier(widthAtTier(1, 4, 1), 1) === true;
  report('AGREE/band: span 3-of-4 at 2 tracks is a band by tier, not by class', ok,
    `w=${w} bandAtTier=${isBandAtTier(w, 2)} bandClass=${isBandClass(3, 4)}`);
}

// The agreement cases above are only worth their line if they would SPEAK UP. Feed
// the comparator the pre-fix rule (band decided by the authored span) and it must
// report a mismatch — otherwise it is a test that cannot fail.
{
  const divergentWidth = (span, cols, tracks) => (tracks === 1 ? 1
    : span >= cols ? tracks
    : tracks === cols ? span
    : Math.max(1, Math.min(2, Math.round(span / cols * 2) + 1)));   // over-wide at the 2-track tier
  let caught = false;
  for (const { span, cols, tracks } of widthCorpus())
    if (widthAtTier(span, cols, tracks) !== divergentWidth(span, cols, tracks)) { caught = true; break; }
  report('AGREE/teeth: the comparator reports a seeded divergence', caught,
    'a deliberately wrong width function was accepted as equal');
}

// ── 6/7. TEXT — the character budget AGREES IN DIRECTION with the render's N ─
// The budget is the one APPROXIMATE check in the static gate, so the only thing
// that earns it its line is DIRECTION: a title the render gate would fail must be
// flagged HERE FIRST, and a title that fits must NOT be flagged at all. An
// advisory that points the other way is worse than no advisory, because the first
// person to see it contradict the verdict learns to ignore it.
//
// Both halves are asserted over ONE cell: N's own predicate is called with the
// SAME available width and the SAME monospace demand the static budget computes
// with, so this is an agreement between the two DECISIONS and not a re-run of one
// of them. N is pure over `m.wordFit`, which is why no browser is needed — the
// same reason the placement cases above can lift the engine's own functions.
const N_CELL_PX = 270.5;   // a span-1 cell of the seed's 4-track, 1246px band grid
const N_FONT_PX = 17;      // .box .t at the two widest tiers

// A measurement stub carrying only what the invariant table reads. Every other
// invariant is free to come back red on it — only N's verdict is read.
function nVerdict(word) {
  const { runInvariants } = require(path.join(ROOT, 'tools', 'validate-layout.cjs'));
  const m = { clipped: 0, overflowX: 0, topZones: [], leftPad: 0, rightPad: 0,
    collisions: [], balloons: [], stackOverflow: [], leafGrids: [], spanRatios: [],
    rootRowMax: 2, halfSlots: [], heights: [], rowTracks: [],
    census: { diffs: [], authored: {}, rendered: {}, nActs: 0, nAuthoredPages: 0, pageId: 'x' },
    wordFit: [{ zone: 'z', k: 'x', word, vertical: false,
      wordW: Math.round(word.length * N_FONT_PX * MONO_ADVANCE_EM),
      availW: Math.round(N_CELL_PX) }] };
  const ctx = { form: 'dashboard', tier: 'ultra', w: 2560, WIDE: true, PASSES: 3,
    deterministic: true, sigs: [], uniqueSigs: [], robustOk: true, robustDetail: '',
    captureOk: true, captureDetail: '' };
  const n = runInvariants(m, ctx).find(c => c.id === 'N');
  return n ? n.ok : null;
}
const staticFlags = word => textBudget({ id: 'x', title: word },
  { availPx: N_CELL_PX, fontPx: N_FONT_PX, half: false, cell: 'cell' })
  .findings.some(f => f.kind === 'token');

// 6 — a token that FAILS N is flagged by the budget, end to end and by predicate.
{
  const LONG = 'Orquestacionmultiregionconsolidada';   // 34 chars, one token
  const cap = capacityFor(N_CELL_PX, N_FONT_PX);
  const budgetFlags = staticFlags(LONG), nOk = nVerdict(LONG);

  // …and the real gate says so on a real deck, naming the numbers. The line must
  // be an [INFO]: the budget is an advisory, so it reports without failing. (The
  // fabricated deck still exits non-zero on the CENSUS — data.generated.js is
  // deliberately not copied — so the exit code cannot carry this assertion; that
  // the budget never fails the gate is case 4's intact-seed ALL PASS.)
  const dir = mkDeck();
  const { p, doc } = loadOverview(dir);
  findNode(doc, 'item-b').title = LONG;
  saveOverview(p, doc);
  const { out } = runNode([CHECK, dir]);
  const line = out.split('\n').find(l => l.includes(`title token "${LONG}"`)) || '';
  const gateSpeaks = line.includes('[INFO]') && line.includes(`is ${LONG.length} char(s)`)
    && line.includes('the cell holds') && line.includes('track(s) in a');
  rmDeck(dir);

  report('TEXT/agree: a token N fails is flagged (advisory) by the static budget',
    budgetFlags === true && nOk === false && gateSpeaks,
    `budget=${budgetFlags} N.ok=${nOk} gate=${gateSpeaks} (${LONG.length} chars vs cap ${cap} @${N_CELL_PX}px) line=${line.trim().slice(0, 120)}`);
}

// 7 — TEETH IN THE OTHER DIRECTION: a token that fits must be flagged by NEITHER.
// Without this the budget could "agree" with N by flagging everything.
{
  const SHORT = 'Orden';                               // 5 chars, comfortably inside
  const cap = capacityFor(N_CELL_PX, N_FONT_PX);
  const budgetFlags = staticFlags(SHORT), nOk = nVerdict(SHORT);

  const dir = mkDeck();
  const { p, doc } = loadOverview(dir);
  findNode(doc, 'item-b').title = SHORT;
  saveOverview(p, doc);
  const { out } = runNode([CHECK, dir]);
  const quiet = !out.includes(`title token "${SHORT}"`);
  rmDeck(dir);

  report('TEXT/teeth: a title that fits is flagged by neither gate',
    budgetFlags === false && nOk === true && quiet,
    `budget=${budgetFlags} N.ok=${nOk} quiet=${quiet} (${SHORT.length} chars vs cap ${cap} @${N_CELL_PX}px)`);
}

console.log(`\n${failures === 0 ? 'OK' : 'FAILED'} — ${failures} guard(s) did not detect their defect.`);
process.exit(failures === 0 ? 0 : 1);
