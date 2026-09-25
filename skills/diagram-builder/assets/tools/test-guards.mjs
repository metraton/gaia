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
import { execFileSync, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import yaml from 'js-yaml';
import { widthAtTier, isBandAtTier, isBandClass, place,
  textBudget, capacityFor, MONO_ADVANCE_EM, isThinRowLeaf, inkBudget } from './check-layout.mjs';

const require = createRequire(import.meta.url);
const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const CHECK = path.join(ROOT, 'tools', 'check-layout.mjs');
const BUILD = path.join(ROOT, 'engine', 'build-data.mjs');
const INDEX = path.join(ROOT, 'index.html');

let failures = 0;
function report(name, ok, detail) {
  console.log(`[${ok ? 'PASS' : 'FAIL'}] ${name}${!ok && detail ? ' — ' + detail : ''}`);
  if (!ok) failures++;
}

// A self-owned minimal fixture. Never copy the consumer deck here: consumers
// are instructed to delete seed pages they do not need, so a test that depends
// on their `overview.yaml` breaks precisely when the scaffold is used
// correctly. The six single cells plus one span-2 cell close a 4×2 rectangle;
// item-1/3/7 provide the three-member `flow` chip used by the CHIP negative.
const FIXTURE_DOCUMENT = {
  title: 'Guard fixture',
  pages: [{
    id: 'overview', name: 'Guard fixture', order: 1, visible: true,
    file: 'pages/overview.yaml',
  }],
};
const FIXTURE_OVERVIEW = {
  id: 'overview',
  layout: 'grid',
  columns: 1,
  filters: [{ key: 'flow', label: 'Fixture flow' }],
  sections: [{
    id: 'section-e',
    title: 'Closed rectangle fixture',
    span: 1,
    columns: 4,
    children: [
      { id: 'item-a', title: 'A' },
      { id: 'item-b', title: 'B' },
      { id: 'item-c', title: 'C', span: 2 },
      { id: 'item-1', title: 'One', filters: ['flow'] },
      { id: 'item-2', title: 'Two' },
      { id: 'item-3', title: 'Three', filters: ['flow'] },
      { id: 'item-7', title: 'Seven', filters: ['flow'] },
    ],
  }],
};

function mkDeck() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'diagram-guard-'));
  fs.mkdirSync(path.join(dir, 'data', 'pages'), { recursive: true });
  fs.writeFileSync(
    path.join(dir, 'data', 'document.yaml'),
    yaml.dump(FIXTURE_DOCUMENT),
    'utf8',
  );
  fs.writeFileSync(
    path.join(dir, 'data', 'pages', 'overview.yaml'),
    yaml.dump(FIXTURE_OVERVIEW),
    'utf8',
  );
  fs.mkdirSync(path.join(dir, 'engine'));
  fs.copyFileSync(BUILD, path.join(dir, 'engine', 'build-data.mjs'));
  // The real stylesheet, so the CSS MIRROR is actually asserted in every case.
  // Without it every fixture here ran with the mirror unread — the guard quiet in
  // the whole suite whose reason for existing is that a quiet guard is the silent
  // false negative. Case 9 removes it again, deliberately, to assert that state.
  fs.copyFileSync(INDEX, path.join(dir, 'index.html'));
  fs.symlinkSync(path.join(ROOT, 'node_modules'), path.join(dir, 'node_modules'), 'dir');
  execFileSync('node', [path.join(dir, 'engine', 'build-data.mjs')], {
    cwd: dir,
    stdio: 'ignore',
  });
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
  // check-layout emits a large report before setting a non-zero exit status.
  // Node may discard buffered pipe output at process shutdown, so capture in a
  // regular temporary file: writes are synchronous and diagnostics survive.
  const captureDir = fs.mkdtempSync(path.join(os.tmpdir(), 'diagram-guard-out-'));
  const capture = path.join(captureDir, 'output.txt');
  const fd = fs.openSync(capture, 'w');
  const result = spawnSync('node', args, { stdio: ['ignore', fd, fd] });
  fs.closeSync(fd);
  const out = fs.readFileSync(capture, 'utf8');
  fs.rmSync(captureDir, { recursive: true, force: true });
  return { code: result.status ?? 1, out: `${out}${result.error?.message || ''}` };
}
// fs.rmSync here is a Node API call inside THIS process, not a chained shell
// `rm` — the T3 gate only classifies Bash-tool commands, so cleanup is not
// expected to be blocked; the try/catch is defensive against OS-level errors.
function rmDeck(dir) {
  if (process.env.DIAGRAM_GUARD_KEEP_FIXTURES === '1') {
    console.log(`[INFO] kept fixture ${dir}`);
    return;
  }
  try { fs.rmSync(dir, { recursive: true, force: true }); } catch { /* best-effort */ }
}
// Rebuild after a mutation: that is the real flow (edit → build → check), and it
// keeps the CENSUS and WORDS checks from reporting the edit as a stale bundle.
function rebuild(dir) {
  execFileSync('node', [path.join(dir, 'engine', 'build-data.mjs')], { cwd: dir, stdio: 'ignore' });
}

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
  report('RECT: section-e short by 1 cell', ok, `exit=${code}\n${out}`);
  rmDeck(dir);
}

// ── 2a. Invariant A, layer 1 — build-data.mjs rejects unknown page form ────
{
  const dir = mkDeck();
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

// ── 2c. SCHEMA — the fields a rail and a box gained, each refused BY NAME ──
// `copy` is a box's copy-to-clipboard button, `indent` a rail's tree step, and a
// rail's colour is one of the four categorical hues. Each rule is probed with a
// value the schema must refuse, after a control that must build — without the
// control a schema that refused every rail would pass the negatives.
{
  const dir = mkDeck();
  const buildInDir = path.join(dir, 'engine', 'build-data.mjs');
  const { p, doc } = loadOverview(dir);
  const it = findNode(doc, 'item-2');

  it.copy = true;
  findNode(doc, 'item-a').copy = 'npm run model';
  saveOverview(p, doc);
  const control = runNode([buildInDir]);
  report('SCHEMA/control: `copy` on a box (true and a string) builds', control.code === 0,
    `exit=${control.code} ${control.out.trim().slice(0, 200)}`);

  const probes = [
    ['copy on a separator', n => { n.type = 'separator'; n.copy = true; delete n.title; },
      '`copy` must be `true` or a non-empty string, and only on a box'],
    ['rail indent 4', n => { n.type = 'rail'; delete n.copy; n.indent = 4; },
      'rail `indent` must be an integer 0..3'],
    ['rail variant good', n => { n.type = 'rail'; delete n.copy; n.variant = 'good'; },
      'unknown rail variant "good"'],
  ];
  const leaked = [];
  for (const [name, mutate, expect] of probes) {
    const fresh = loadOverview(dir);
    const node = findNode(fresh.doc, 'item-2');
    for (const k of ['type', 'copy', 'indent', 'variant']) delete node[k];
    node.title = 'Two';
    mutate(node);
    saveOverview(fresh.p, fresh.doc);
    const { code, out } = runNode([buildInDir]);
    if (!(code !== 0 && out.includes('[strict-schema]') && out.includes(expect))) leaked.push(`${name}(exit=${code})`);
  }
  report('SCHEMA: copy off a box, rail indent past 3, a non-hue rail variant are refused',
    leaked.length === 0, `accepted: ${leaked.join(', ')}`);
  rmDeck(dir);
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
  report('CHIP: orphan + dangling key + arity-1', ok, `exit=${code}\n${out}`);
  rmDeck(dir);
}

// ── 3b. LIT — a filter on a separator passes CHIP and can never light ───────
// The engine stamps `data-filters` only in buildBox and buildRail; a
// separator/spacer node carries none, so its chip membership closes the CHIP
// join while the render never spotlights that end. The strict schema
// legitimately accepts `filters` on a separator — the defect is check-layout's
// to catch.
{
  const dir = mkDeck();
  const { p, doc } = loadOverview(dir);
  const it = findNode(doc, 'item-2');
  it.type = 'separator';
  it.filters = ['flow'];
  saveOverview(p, doc);
  rebuild(dir);
  const { code, out } = runNode([CHECK, dir]);
  const ok = code !== 0 && out.includes('separator "item-2"') && out.includes('never lights');
  report('LIT: filters on a separator cannot light', ok, `exit=${code}\n${out}`);
  rmDeck(dir);
}

// ── 3c. RAILT — a rail title past the two-line ceiling must FAIL the gate ───
// A horizontal no-rowspan rail sits in an `auto` row and `.rail-title` has no
// clamp, so an over-wrapped title does not clip — it grows the row and every
// stack built on the thin-row arithmetic. The negative: a rail whose title
// needs three lines in its 1-of-4 track must be a HARD RAILT fail.
{
  const dir = mkDeck();
  const { p, doc } = loadOverview(dir);
  const it = findNode(doc, 'item-2');
  it.type = 'rail';
  it.title = 'Coordination handshake verification ledger reconciliation';
  saveOverview(p, doc);
  rebuild(dir);
  const { code, out } = runNode([CHECK, dir]);
  const ok = code !== 0 && out.includes('RAILT') && out.includes('item-2')
    && out.includes('ceiling is 2');
  report('RAILT: a three-line rail title fails the static gate', ok, `exit=${code}\n${out}`);
  rmDeck(dir);
}

// ── 3d. U/rail — the render gate's rail-row band must SPEAK UP both ways ────
// The render gate's U models the same thin-row rule the engine and the static
// gate carry (the third copy — measured: teaching only two of the three turned
// every rail row into a false 'must stay 130px' dura failure). A rail thin row
// is `auto`, asserted as the band 33..48: a 130px track means the auto row was
// never applied, a 52px track means a third title line slipped past RAILT's
// static estimate. Both directions are exercised, plus the in-band positive.
{
  const { INVARIANTS } = require(path.join(ROOT, 'tools', 'validate-layout.cjs'));
  const U = INVARIANTS.find(inv => inv.id === 'U' && inv.name === 'uniform slot height');
  const mFor = tracks => ({
    heights: [130], halfSlots: [],
    rowTracks: [{ zone: 'fixture', tracks,
      rows: tracks.map(() => ({ n: 1, sepH: 0, hole: 0, railH: 1 })), overflow: [] }],
  });
  const notApplied = U.check(mFor([130]));
  const threeLines = U.check(mFor([52]));
  const inBand = U.check(mFor([33, 48]));
  const ok = U && notApplied.ok === false && notApplied.detail.includes('rail row is auto')
    && threeLines.ok === false && inBand.ok === true;
  report('U/rail: a rail row outside 33..48px fails the render gate, in-band passes', ok,
    `130px -> ${notApplied && notApplied.ok} | 52px -> ${threeLines && threeLines.ok} | 33/48px -> ${inBand && inBand.ok}`);
}

// ── 3e. U/compact — a short row is legal ONLY in a grid that declares it ────
// `compact` (index.html `.zone.compact`) gives one leaf grid a shorter row. The
// render gate must fail a grid that runs that row WITHOUT declaring it, and
// accept the same row once it does. Box rows (railH 0), so the band rule for
// rails does not apply.
{
  const { INVARIANTS } = require(path.join(ROOT, 'tools', 'validate-layout.cjs'));
  const U = INVARIANTS.find(inv => inv.id === 'U' && inv.name === 'uniform slot height');
  const mFor = (compact) => ({
    heights: [130], halfSlots: [],
    rowTracks: [{ zone: 'fixture', tracks: [74, 74], cellH: 74, compact,
      rows: [0, 1].map(() => ({ n: 1, sepH: 0, hole: 0, railH: 0 })), overflow: [] }],
  });
  const undeclared = U.check(mFor(false));
  const declared = U.check(mFor(true));
  const ok = U && undeclared.ok === false && undeclared.detail.includes('without the `compact` treatment')
    && declared.ok === true;
  report('U/compact: a 74px row fails without `compact`, passes with it', ok,
    `undeclared -> ${undeclared && undeclared.ok} | declared -> ${declared && declared.ok}\n${undeclared && undeclared.detail}`);
}

// ── 3f. U/thin — a sep+spacer row is thin, a trailing thin row is a floor ───
// The engine thins a row of rules and DECLARED HOLES alike, and lets a trailing
// one absorb a stretched section's slack (minmax(--sep-row-h, 1fr)). U must hold
// both: a mixed separator+spacer row at 40px passes, a trailing one at 72px
// passes, the same 72px on a NON-trailing row fails.
{
  const { INVARIANTS } = require(path.join(ROOT, 'tools', 'validate-layout.cjs'));
  const U = INVARIANTS.find(inv => inv.id === 'U' && inv.name === 'uniform slot height');
  const thinRow = { n: 2, sepH: 1, hole: 1, railH: 0 };
  const boxRow = { n: 1, sepH: 0, hole: 0, railH: 0 };
  const mFor = (tracks, rows) => ({ heights: [130], halfSlots: [],
    rowTracks: [{ zone: 'fixture', tracks, rows, overflow: [] }] });
  const mixed = U.check(mFor([130, 40, 130], [boxRow, thinRow, boxRow]));
  const trailing = U.check(mFor([130, 72], [boxRow, thinRow]));
  const interior = U.check(mFor([130, 72, 130], [boxRow, thinRow, boxRow]));
  const ok = mixed.ok === true && trailing.ok === true && interior.ok === false;
  report('U/thin: sep+spacer row is thin; only a TRAILING thin row may grow', ok,
    `mixed -> ${mixed.ok} | trailing 72 -> ${trailing.ok} | interior 72 -> ${interior.ok}`);
}

// ── 3g. SPAN — a stylesheet without the partial-span rules FAILS the gate ───
// Every width the static gate reports assumes a span of M occupies M tracks, and
// that assumption is four CSS rules. Remove the two `.mspan` rules and the model
// stays internally consistent while the browser auto-places the section into one
// track — so the gate, not a render, must say so.
{
  const dir = mkDeck();
  const idx = path.join(dir, 'index.html');
  const src = fs.readFileSync(idx, 'utf8');
  const rule = 'grid-column:span var(--span, 1); }';
  const had = src.split(rule).length - 1;
  fs.writeFileSync(idx, src.split(rule).join('}'), 'utf8');
  const { code, out } = runNode([CHECK, dir]);
  const ok = had >= 2 && code !== 0 && out.includes('SPAN') && out.includes('implements no')
    && out.includes('a partial span occupies --span tracks');
  report('SPAN: a stylesheet missing the partial-span rules fails the static gate', ok,
    `rules-removed=${had} exit=${code}\n${out}`);
  rmDeck(dir);
}

// ── 3h. WORDS — a text-only edit without a rebuild FAILS the gate ───────────
// CENSUS compares ids and counts, so rewording a title leaves it green while the
// bundle still carries the old words. The fixture is edited and deliberately NOT
// rebuilt: WORDS must name the page and the string it could not find.
{
  const dir = mkDeck();
  const { p, doc } = loadOverview(dir);
  findNode(doc, 'item-a').title = 'Reworded without a build';
  saveOverview(p, doc);
  const { code, out } = runNode([CHECK, dir]);
  const ok = code !== 0 && out.includes('page "overview"')
    && out.includes('NOT in data/data.generated.js') && out.includes('Reworded without a build');
  report('WORDS: a reworded title with a stale bundle fails the static gate', ok, `exit=${code}\n${out}`);
  rmDeck(dir);
}

// ── 3i. INK — the height budget reports an overflow and a fit, by slack ─────
// INK runs only on the pages a deck lists in INK_PAGES, which the seed ships
// empty, so the budget itself is exercised as a pure function: a `half` box
// carrying a kicker, a title and three description lines cannot fit the ~63px
// it gets (negative slack), while a title-only full box fits its 130px row.
{
  const ctx = { availPx: 200, fontPx: 17 };
  const over = inkBudget({ id: 'x', kicker: 'K', title: 'Title', description: ['one', 'two', 'three'] },
    { ...ctx, half: true });
  const fits = inkBudget({ id: 'y', title: 'Title' }, { ...ctx, half: false });
  const skip = inkBudget({ id: 'z', type: 'separator' }, { ...ctx, half: false });
  const ok = over && over.slack < 0 && fits && fits.slack > 0 && skip === null;
  report('INK: an overfull half box has negative slack, a title-only box fits, a separator is skipped', ok,
    `over=${over && over.slack.toFixed(1)} fits=${fits && fits.slack.toFixed(1)} skip=${skip}`);
}

// ── 3j. FILL / TXT — the ratchet rows speak up on a pure measurement ────────
// Both are page-scoped (RATCHET_PAGES, empty in the seed), so their `check` is
// called directly: a root span rendered at 40% of its declared width and a
// clamped description must fail, and the clean measurements must pass.
{
  const { INVARIANTS } = require(path.join(ROOT, 'tools', 'validate-layout.cjs'));
  const FILL = INVARIANTS.find(inv => inv.id === 'FILL');
  const TXT = INVARIANTS.find(inv => inv.id === 'TXT');
  const fillBad = FILL.check({ spanFill: [{ id: 's', span: 5, cols: 6, w: 400, expected: 1000, offPct: 60 }] });
  const fillOk = FILL.check({ spanFill: [{ id: 's', span: 5, cols: 6, w: 999, expected: 1000, offPct: 0.1 }] });
  const txtBad = TXT.check({ clamps: [{ id: 'b', part: 'desc', over: 14, text: 'cut' }], nBoxes: 1 });
  const txtOk = TXT.check({ clamps: [], nBoxes: 1 });
  const ok = fillBad.ok === false && fillOk.ok === true && txtBad.ok === false && txtOk.ok === true;
  report('FILL/TXT: a short root span and a clamped block fail, clean measurements pass', ok,
    `fill 60% -> ${fillBad.ok} | fill 0.1% -> ${fillOk.ok} | txt cut -> ${txtBad.ok} | txt clean -> ${txtOk.ok}`);
}

// ── 4. control positive — the intact owned fixture must pass ───────────────
{
  const dir = mkDeck();
  const { code, out } = runNode([CHECK, dir]);
  const ok = code === 0 && out.includes('ALL PASS');
  report('control: intact owned fixture passes', ok, `exit=${code}\n${out}`);
  rmDeck(dir);
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

// ── 5b. AGREE/thin — the THIN-ROW predicate, engine vs gate ────────────────
// isThinRowLeaf decides which rows escape --cell-h, and it exists twice for the
// same CORS reason as the placement model. The corpus walks every leaf kind the
// schema can author — separator / rail / spacer / box, each horizontal and
// vertical, with and without rowspan — plus the non-leaves (a section, null):
// the rail admission has three edges (vertical excluded, rowspan excluded,
// horizontal-no-rowspan admitted) and each edge is a case here.
function thinCorpus() {
  const out = [null, undefined, { id: 'sec', children: [] },
    { id: 'sec-rail', type: 'rail', children: [] }];
  for (const type of [undefined, 'box', 'separator', 'rail', 'spacer'])
    for (const treatment of [undefined, [], ['vertical'], ['centered']])
      for (const rowspan of [undefined, 1, 2, '2']) {
        const leaf = { id: 'x' };
        if (type !== undefined) leaf.type = type;
        if (treatment !== undefined) leaf.treatment = treatment;
        if (rowspan !== undefined) leaf.rowspan = rowspan;
        out.push(leaf);
      }
  return out;
}

{
  let mismatch = null;
  try {
    const { isThinRowLeaf: engineThin } = liftFromEngine('isThinRowLeaf');
    for (const leaf of thinCorpus()) {
      const mine = !!isThinRowLeaf(leaf), theirs = !!engineThin(leaf);
      if (mine !== theirs) {
        mismatch = `${JSON.stringify(leaf)}: gate ${mine} vs engine ${theirs}`;
        break;
      }
    }
  } catch (e) { mismatch = e.message; }
  report('AGREE/thin: gate isThinRowLeaf == engine isThinRowLeaf', mismatch === null, mismatch);
}

// The thin comparator is only worth its line if it would SPEAK UP. Feed it the
// PRE-RAIL rule (separator/spacer only — the exact predicate the rail admission
// replaced) and it must report a mismatch on the horizontal no-rowspan rail.
{
  const divergentThin = c => c && !Array.isArray(c.children) &&
    (c.type === 'spacer' ||
      (c.type === 'separator' && !(Array.isArray(c.treatment) ? c.treatment : []).includes('vertical')));
  let caught = false;
  for (const leaf of thinCorpus())
    if (!!isThinRowLeaf(leaf) !== !!divergentThin(leaf)) { caught = true; break; }
  report('AGREE/thin-teeth: the thin comparator reports a seeded divergence', caught,
    'the pre-rail thin predicate was accepted as equal');
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
  // be an [INFO]: the budget is an advisory, so it reports without failing. The
  // mutation is not rebuilt, so the deck exits non-zero on WORDS and the exit code
  // cannot carry this assertion; that the budget never fails the gate is case 4's
  // intact-fixture ALL PASS.
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

// ── 8. SPACER — the payload rejection, and the bare spacer that must pass ──
// A `spacer` is the one leaf that reads NO payload, and the whole value of the
// type is that "a cell held open" and "a card with nothing in it" stay
// distinguishable. Only the schema can hold that line: an ignored payload key
// renders identically to an absent one, so a spacer carrying a title would look
// exactly like a working spacer while the author believed it said something.
// Each payload slot is therefore probed SEPARATELY and must be refused BY NAME —
// a single blanket "invalid spacer" would pass this suite while leaving the
// author to guess which of five keys was the problem. The positive control runs
// FIRST, on the same fixture and the same spacer: without it, a schema that
// rejected every spacer would score five out of five here.
{
  const dir = mkDeck();
  const buildInDir = path.join(dir, 'engine', 'build-data.mjs');
  const { p, doc } = loadOverview(dir);
  const probe = { id: 'probe-spacer', type: 'spacer', order: 99 };
  findNode(doc, 'section-e').children.push(probe);

  // The control: geometry-only, and legal. A bare spacer must BUILD.
  saveOverview(p, doc);
  const bare = runNode([buildInDir]);
  report('SPACER/control: a bare spacer builds', bare.code === 0,
    `exit=${bare.code} ${bare.out.trim().slice(0, 160)}`);

  const leaked = [];
  for (const key of ['kicker', 'title', 'description', 'detail', 'note']) {
    for (const k of ['kicker', 'title', 'description', 'detail', 'note']) delete probe[k];
    probe[key] = key === 'description' ? ['a line'] : 'not a card';
    saveOverview(p, doc);
    const { code, out } = runNode([buildInDir]);
    const refused = code !== 0 && out.includes('[strict-schema]')
      && out.includes(`a \`spacer\` carries no "${key}"`)
      && out.includes('it is a box: drop `type: spacer`');
    if (!refused) leaked.push(`${key}(exit=${code})`);
  }
  report('SPACER: every payload key on a spacer is refused by name',
    leaked.length === 0, `accepted: ${leaked.join(', ')}`);
  rmDeck(dir);
}

// ── 9. CSS MIRROR — the guard that can go QUIET, in both directions ────────
// Every OTHER case here seeds a defect and asserts the gate speaks. This one
// seeds a defect in the gate's own READING and asserts the gate does not stay
// silent about it — the failure mode of a MIRRORED assertion, which no other
// check in this suite has: the mirrored tokens sit behind brittle regexes over
// `.box { }` / `:root { }`, so one reordered or renamed declaration unasserts
// them AND the whole TEXT budget derived from them, with nothing failing.
// The two directions are asserted separately because they must NOT be the same
// verdict: a stylesheet that is PRESENT and unreadable is a FAILURE (the
// declaration moved past the probe and every derived number is unverified),
// while NO stylesheet is a data-only fixture — not asserted, counted, and never
// a pass. Reversed, the first one is exactly the silence that certifies drift.
{
  const dir = mkDeck();
  const idx = path.join(dir, 'index.html');
  const src = fs.readFileSync(idx, 'utf8');
  // `--frame-h:40px;` is declared EXACTLY ONCE and is read by the frameH probe,
  // so removing that one substring is a stylesheet that still parses, still
  // renders, and no longer answers one of the questions the mirror asks.
  const probed = '--frame-h:40px;';
  const removed = src.includes(probed);
  fs.writeFileSync(idx, src.replace(probed, ''), 'utf8');
  const { code, out } = runNode([CHECK, dir]);
  const ok = removed && code !== 0
    && out.includes('declares no readable [frameH]')
    && !out.includes('ALL PASS');
  report('CSS/mirror: a deleted probed declaration FAILS the gate', ok,
    `probe-present=${removed} exit=${code}\n${out}`);
  rmDeck(dir);
}
{
  const dir = mkDeck();
  fs.rmSync(path.join(dir, 'index.html'));
  const { code, out } = runNode([CHECK, dir]);
  const ok = code !== 0
    && out.includes('[NOT ASSERTED]')
    && out.includes('NOT ASSERTED —')
    && !out.includes('ALL PASS');
  report('CSS/mirror: an absent stylesheet is NOT ASSERTED, never a pass', ok,
    `exit=${code}\n${out}`);
  rmDeck(dir);
}

console.log(`\n${failures === 0 ? 'OK' : 'FAILED'} — ${failures} guard(s) did not detect their defect.`);
process.exit(failures === 0 ? 0 : 1);
