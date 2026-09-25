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
  textBudget, capacityFor, MONO_ADVANCE_EM, isThinRowLeaf, inkBudget,
  railTitleFit, railTitleWidth, headerBudget, predictPageHeight, pageHeightAdvisory,
  CSS_TEXT } from './check-layout.mjs';
import { DEFAULT_TOKENS } from '../engine/tokens.mjs';

const require = createRequire(import.meta.url);
const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const CHECK = path.join(ROOT, 'tools', 'check-layout.mjs');
const BUILD = path.join(ROOT, 'engine', 'build-data.mjs');
const TOKENS_MODULE = path.join(ROOT, 'engine', 'tokens.mjs');
const INDEX = path.join(ROOT, 'index.html');
// Every fixture number below derives from the defaults, so a moved default moves
// the fixtures with it. The render gate's expectations are set from them too:
// a case that calls an invariant directly runs against DEFAULT_TOKENS.
const T = DEFAULT_TOKENS;
const VALIDATE = require(path.join(ROOT, 'tools', 'validate-layout.cjs'));
VALIDATE.applyTokens(T);
const RX = VALIDATE.renderExpectations(T);

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
  fs.copyFileSync(TOKENS_MODULE, path.join(dir, 'engine', 'tokens.mjs'));
  fs.copyFileSync(path.join(ROOT, 'engine', 'chips.cjs'), path.join(dir, 'engine', 'chips.cjs'));
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
    heights: [T.row.cell_h], halfSlots: [],
    rowTracks: [{ zone: 'fixture', tracks,
      rows: tracks.map(() => ({ n: 1, sepH: 0, hole: 0, railH: 1 })), overflow: [] }],
  });
  const notApplied = U.check(mFor([T.row.cell_h]));
  const threeLines = U.check(mFor([RX.RAIL_ROW_MAX + 4]));
  const inBand = U.check(mFor([RX.RAIL_ROW_MIN, RX.RAIL_ROW_MAX]));
  const ok = U && notApplied.ok === false && notApplied.detail.includes('rail row is auto')
    && threeLines.ok === false && inBand.ok === true;
  report(`U/rail: a rail row outside ${RX.RAIL_ROW_MIN}..${RX.RAIL_ROW_MAX}px fails the render gate, in-band passes`, ok,
    `cell -> ${notApplied && notApplied.ok} | over -> ${threeLines && threeLines.ok} | band -> ${inBand && inBand.ok}`);
}

// ── 3e. U/compact — a short row is legal ONLY in a grid that declares it ────
// `compact` (index.html `.zone.compact`) gives one leaf grid a shorter row. The
// render gate must fail a grid that runs that row WITHOUT declaring it, and
// accept the same row once it does. Box rows (railH 0), so the band rule for
// rails does not apply.
{
  const { INVARIANTS } = require(path.join(ROOT, 'tools', 'validate-layout.cjs'));
  const U = INVARIANTS.find(inv => inv.id === 'U' && inv.name === 'uniform slot height');
  const short = T.row.compact_h;
  const mFor = (compact) => ({
    heights: [T.row.cell_h], halfSlots: [],
    rowTracks: [{ zone: 'fixture', tracks: [short, short], cellH: short, compact,
      declaredCellH: compact ? short : undefined,
      rows: [0, 1].map(() => ({ n: 1, sepH: 0, hole: 0, railH: 0 })), overflow: [] }],
  });
  const undeclared = U.check(mFor(false));
  const declared = U.check(mFor(true));
  const ok = U && undeclared.ok === false && undeclared.detail.includes('without a declared row override')
    && declared.ok === true;
  report(`U/compact: a ${short}px row fails without a declared override, passes with it`, ok,
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
  const cell = T.row.cell_h, sep = T.row.sep_h, grown = T.row.sep_h + 32;
  const mFor = (tracks, rows) => ({ heights: [cell], halfSlots: [],
    rowTracks: [{ zone: 'fixture', tracks, rows, overflow: [] }] });
  const mixed = U.check(mFor([cell, sep, cell], [boxRow, thinRow, boxRow]));
  const trailing = U.check(mFor([cell, grown], [boxRow, thinRow]));
  const interior = U.check(mFor([cell, grown, cell], [boxRow, thinRow, boxRow]));
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

// ── 3k. SLICE — a grid root's row packing is not a wrap ────────────────────
// A root with bands is a CSS grid: four span-1 sections in a 2-column root pack
// into two rows BY DESIGN (expectedLines 2), while the same two lines in a flex
// row (expectedLines 1), or a third line in the grid, is the wrap SLICE names.
{
  const { INVARIANTS } = require(path.join(ROOT, 'tools', 'validate-layout.cjs'));
  const SLICE = INVARIANTS.find(inv => inv.id === 'SLICE');
  const row = (lines, expectedLines) => ({ compoundRows: [{ zone: '(root)', n: 4, lines, expectedLines,
    equalSpans: true, wSpread: 0, column: false,
    slices: [0, 1, 2, 3].map(i => ({ id: `s${i}`, w: 600, top: i < 2 ? 0 : 300, span: 1 })) }] });
  const packed = SLICE.check(row(2, 2));
  const flexWrap = SLICE.check(row(2, 1));
  const gridWrap = SLICE.check(row(3, 2));
  const ok = packed.ok === true && flexWrap.ok === false && gridWrap.ok === false
    && flexWrap.detail.includes('pack into 1');
  report('SLICE: a grid root packing its spans into rows passes, a line beyond the packing fails', ok,
    `packed=${packed.ok} flexWrap=${flexWrap.ok} gridWrap=${gridWrap.ok}`);
}

// ── 3j. FILL / TXT — the ratchet rows speak up on a pure measurement ────────
// Both run on every page not declared `text_fit: advisory`; their `check` is
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
const N_FONT_PX = T.type.title.max_px;   // .box .t at the two widest tiers

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

// ── 10. TEXT FIT AT THE PRESENTATION VIEWPORT ──────────────────────────────
// A description needing more lines than `.box .desc` clamps FAILS at the
// document.yaml `viewport` width and stays advisory at every other tier; moving
// the viewport moves the failing tier with it. The fixture's 4-track band gives
// item-a a ~270px cell at every wide tier, so four authored lines always need 4.
const writeDocument = (dir, extra) => fs.writeFileSync(path.join(dir, 'data', 'document.yaml'),
  yaml.dump({ ...FIXTURE_DOCUMENT, ...extra }), 'utf8');
const FOUR_LINES = ['first line', 'second line', 'third line', 'a fourth line'];
{
  const dir = mkDeck();
  const { p, doc } = loadOverview(dir);
  findNode(doc, 'item-a').description = FOUR_LINES;
  saveOverview(p, doc);
  rebuild(dir);
  const atDefault = runNode([CHECK, dir]);
  writeDocument(dir, { tokens: { viewport: { w: 2560, h: 1440 } } });
  rebuild(dir);
  const atWide = runNode([CHECK, dir]);
  const failLine = w => `[FAIL] overview:root > section-e > item-a @${w}px: description needs 4`;
  const ok = atDefault.code !== 0 && atDefault.out.includes(failLine(1920))
    && atDefault.out.includes('presentation tier')
    && atWide.code !== 0 && atWide.out.includes(failLine(2560)) && !atWide.out.includes(failLine(1920));
  report('TEXT/viewport: desc-lines past the clamp fail at the viewport tier, and follow it', ok,
    `default exit=${atDefault.code} wide exit=${atWide.code}\n${atDefault.out}\n${atWide.out}`);
  rmDeck(dir);
}

// ── 10b. text_fit: advisory — the one opt-out, and a closed enum ───────────
// The same overflow on a page declaring `text_fit: advisory` is reported and
// passes; an unknown `text_fit` or an out-of-range viewport is refused by the
// build, so an opt-out cannot be a typo.
{
  const dir = mkDeck();
  const { p, doc } = loadOverview(dir);
  findNode(doc, 'item-a').description = FOUR_LINES;
  doc.text_fit = 'advisory';
  saveOverview(p, doc);
  rebuild(dir);
  const advised = runNode([CHECK, dir]);
  const buildInDir = path.join(dir, 'engine', 'build-data.mjs');
  doc.text_fit = 'loose';
  saveOverview(p, doc);
  const badFit = runNode([buildInDir]);
  doc.text_fit = 'strict';
  saveOverview(p, doc);
  writeDocument(dir, { tokens: { viewport: { w: 10, h: 1080 } } });
  const badViewport = runNode([buildInDir]);
  const ok = advised.code === 0 && advised.out.includes('[INFO] overview:root > section-e > item-a: description needs 4')
    && badFit.code !== 0 && badFit.out.includes('unknown page text_fit "loose"')
    && badViewport.code !== 0 && badViewport.out.includes('tokens.viewport.w must be an integer 320..7680 px');
  report('TEXT/opt-out: text_fit advisory reports without failing; a bad text_fit or viewport is refused', ok,
    `advised exit=${advised.code} badFit exit=${badFit.code} badViewport exit=${badViewport.code}\n` +
    `${advised.out}\n${badFit.out}\n${badViewport.out}`);
  rmDeck(dir);
}

// ── 10c. COMPACT — the short row and the released clamp are modelled ───────
// A `compact` grid runs a 74px row with no description clamp, so the clamp
// cannot hide a long description: the static gate reports no desc-lines finding
// there, and INK measures the whole description against the short slot.
{
  const ctx = { availPx: 270, fontPx: 17 };
  const three = { id: 'c', title: 'Title', description: ['one', 'two', 'three'] };
  const four = { id: 'd', title: 'Title', description: FOUR_LINES };
  const over = inkBudget(three, { ...ctx, compact: true });
  const fits = inkBudget({ id: 'e', title: 'Title', description: ['one'] }, { ...ctx, compact: true });
  const normal = inkBudget(three, ctx);
  const clampKinds = b => b.findings.map(f => f.kind);
  const ok = over.slot === CSS_TEXT.compactCellH && over.slack < 0 && fits.slack > 0 && normal.slack > 0
    && !clampKinds(textBudget(four, { ...ctx, compact: true, form: 'dashboard', cell: 'c' })).includes('desc-lines')
    && clampKinds(textBudget(four, { ...ctx, form: 'dashboard', cell: 'c' })).includes('desc-lines');
  report('COMPACT: a 3-line description overflows the 74px row, one line fits, the clamp is released', ok,
    `over=${over.slot}/${over.slack.toFixed(1)} fits=${fits.slack.toFixed(1)} normal=${normal.slack.toFixed(1)}`);
}

// ── 10d. RAILT/indent — the indent shrinks the width the title wraps in ────
// An indented rail draws its frame on the title, inset by indent × --indent-step
// and padded on both sides, so a title that fits two lines flat wraps past the
// ceiling at indent 3 in the same cell.
{
  const title = 'Coordination handshake ledger';
  const flat = railTitleFit(200, { type: 'rail', title, indent: 0 });
  const deep = railTitleFit(200, { type: 'rail', title, indent: 3 });
  const expectW = 200 - 2 * CSS_TEXT.railBorder - 2 * CSS_TEXT.indentStep - CSS_TEXT.boxPad
    - 2 * (CSS_TEXT.railIndentTitlePadX + CSS_TEXT.railIndentTitleBorder);
  const ok = flat.lines <= CSS_TEXT.railTitleLines && deep.lines > CSS_TEXT.railTitleLines
    && railTitleWidth(200, { type: 'rail', indent: 2 }) === expectW;
  report('RAILT/indent: a title that fits flat wraps past the ceiling at indent 3', ok,
    `flat=${flat.lines}ln@${flat.px}px deep=${deep.lines}ln@${deep.px}px`);
}

// ── 10e. HEADER — section title and subtitle against their clamps ──────────
{
  const long = headerBudget({ title: 'Coordination handshake verification ledger reconciliation',
    subtitle: 'one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen' },
  200, 1920);
  const short = headerBudget({ title: 'Short title', subtitle: 'A short subtitle' }, 200, 1920);
  const kinds = long.findings.map(f => f.kind);
  const ok = kinds.includes('header-title-lines') && kinds.includes('header-sub-lines')
    && short.findings.length === 0 && short.titleLines === 1;
  report('HEADER: a long section title and subtitle overflow their clamps, a short header fits', ok,
    `long=${kinds.join(',')} (${long.titleLines}/${long.subLines} ln) short=${short.findings.length}`);
}

// ── 10f. HEIGHT — the predicted page height advises past the viewport ──────
{
  const boxes = n => [...Array(n).keys()].map(i => ({ id: `b${i}`, title: `Box ${i}` }));
  const pageOf = n => ({ id: 'h', columns: 1, sections: [{ id: 's', title: 'S', columns: 1, children: boxes(n) }] });
  const vp = { w: 1920, h: 1080 };
  const tall = predictPageHeight(pageOf(12), vp.w);
  const small = predictPageHeight(pageOf(2), vp.w);
  const expected = 12 * CSS_TEXT.cellH + 11 * CSS_TEXT.gap;
  const note = pageHeightAdvisory(tall.totalPx, vp);
  const ok = tall.contentPx > expected && note && /^predicted \d+px > 1080 \(\+\d+\) at 1920$/.test(note)
    && pageHeightAdvisory(small.totalPx, vp) === null;
  report('HEIGHT: a 12-row page is predicted past 1080, a 2-row page fits', ok,
    `tall=${Math.round(tall.totalPx)} small=${Math.round(small.totalPx)} note=${note}`);
}

// ── 12. CORE CHIPS, CHIP-X, HARMONY, LEAD — the deck-level chip rules ──────
// A second page lets a chip cross pages. Its two boxes close a 2-track row, and
// its `flow` chip matches the fixture's label unless a case changes it.
const CORE = { key: 'core', label: 'Is it core?' };
function withSecondPage(dir, { entry = {}, flowLabel = 'Fixture flow', core = true } = {}) {
  fs.writeFileSync(path.join(dir, 'data', 'pages', 'second.yaml'), yaml.dump({
    id: 'second', columns: 1, filters: [{ key: 'flow', label: flowLabel }],
    sections: [{ id: 'second-s', title: 'Second page', columns: 2, children: [
      { id: 'second-a', title: 'A', filters: ['flow'] }, { id: 'second-b', title: 'B', filters: ['flow'] }] }],
  }), 'utf8');
  writeDocument(dir, { ...(core ? { filters: [CORE] } : {}), pages: [...FIXTURE_DOCUMENT.pages,
    { id: 'second', name: 'Second', order: 2, visible: true, file: 'pages/second.yaml', ...entry }] });
}
function chipCore(doc) { for (const id of ['item-a', 'item-b']) findNode(doc, id).filters = ['core']; }
const bundleKeys = dir => require(path.join(ROOT, 'tools', 'static-census.cjs')).loadGenerated(dir).doc.pages
  .map(p => `${p.id}:${(p.filters || []).map(f => f.key).join('+')}`).join(' ');
{
  const dir = mkDeck();
  const { p, doc } = loadOverview(dir);
  chipCore(doc);
  saveOverview(p, doc);
  withSecondPage(dir, { entry: { omit_filters: ['core'] } });
  const built = runNode([path.join(dir, 'engine', 'build-data.mjs')]);
  const keys = built.code === 0 ? bundleKeys(dir) : '';
  const quiet = runNode([CHECK, dir]);
  const ok = built.code === 0 && keys === 'overview:core+flow second:flow'
    && quiet.code === 0 && quiet.out.includes('ALL PASS');
  report('CORE/inherit: core chips come first on every page, an omitted one is gone, the deck passes', ok,
    `build exit=${built.code} keys="${keys}" check exit=${quiet.code}\n${built.out}\n${quiet.out.slice(-1500)}`);
  rmDeck(dir);
}
{
  const dir = mkDeck();
  const buildInDir = path.join(dir, 'engine', 'build-data.mjs');
  const { p, doc } = loadOverview(dir);
  chipCore(doc);
  const misses = [];
  const expect = (needle, setup) => { setup(); const r = runNode([buildInDir]);
    if (!(r.code !== 0 && r.out.includes(needle))) misses.push(`${needle} (exit=${r.code}) ${r.out.slice(0, 300)}`); };
  expect('redeclares the core chip with a different label', () => {
    saveOverview(p, { ...doc, filters: [...doc.filters, { key: 'core', label: 'Core?' }] });
    withSecondPage(dir, { entry: { omit_filters: ['core'] } }); });
  expect('which is not a core chip', () => {
    saveOverview(p, doc); withSecondPage(dir, { entry: { omit_filters: ['flow'] } }); });
  saveOverview(p, { ...doc, filters: [...doc.filters, { ...CORE }] });
  withSecondPage(dir, { entry: { omit_filters: ['core'] } });
  const same = runNode([buildInDir]);
  if (same.code !== 0) misses.push(`an identical redeclaration must build (exit=${same.code}) ${same.out.slice(0, 300)}`);
  saveOverview(p, doc);
  withSecondPage(dir);
  rebuild(dir);
  const unomitted = runNode([CHECK, dir]);
  if (!(unomitted.code !== 0 && unomitted.out.includes('page "second" chip "core"')))
    misses.push(`an inherited chip with no member must fail CHIP (exit=${unomitted.code})`);
  report('CORE/refuse: a changed redeclaration and a non-core omission are refused; an unused core chip fails CHIP',
    misses.length === 0, misses.join('\n'));
  rmDeck(dir);
}
{
  const dir = mkDeck();
  withSecondPage(dir, { flowLabel: 'Another flow', core: false });
  rebuild(dir);
  const split = runNode([CHECK, dir]);
  withSecondPage(dir, { core: false });
  rebuild(dir);
  const agreed = runNode([CHECK, dir]);
  const ok = split.code !== 0 && split.out.includes('[FAIL] chip "flow": carries 2 labels across pages')
    && agreed.code === 0 && !agreed.out.includes('[FAIL] chip "flow"');
  report('CHIP-X: one key with two labels across pages fails, one label passes', ok,
    `split exit=${split.code} agreed exit=${agreed.code}\n${split.out.slice(-1200)}`);
  rmDeck(dir);
}
{
  const dir = mkDeck();
  const { p, doc } = loadOverview(dir);
  writeDocument(dir, { harmony: true });
  rebuild(dir);
  const loose = runNode([CHECK, dir]);
  for (const id of ['item-a', 'item-b', 'item-c', 'item-2']) findNode(doc, id).filters = ['flow'];
  doc.sections.unshift({ id: 'lead', lead: true, order: 0, title: 'The fixture claim' });
  saveOverview(p, doc);
  rebuild(dir);
  const tight = runNode([CHECK, dir]);
  writeDocument(dir, { harmony: 'yes' });
  const badSwitch = runNode([path.join(dir, 'engine', 'build-data.mjs')]);
  const ok = loose.code !== 0 && loose.out.includes('[FAIL] page "overview" box "item-a": belongs to no chip')
    && tight.code === 0 && !tight.out.includes('box "lead"')
    && badSwitch.code !== 0 && badSwitch.out.includes('`harmony` is true or false');
  report('HARMONY: an unchipped box fails when the deck opts in, the lead band is exempt, the switch is closed', ok,
    `loose exit=${loose.code} tight exit=${tight.code} bad exit=${badSwitch.code}\n${tight.out.slice(-1500)}`);
  rmDeck(dir);
}
{
  const dir = mkDeck();
  const buildInDir = path.join(dir, 'engine', 'build-data.mjs');
  const { p, doc } = loadOverview(dir);
  const lead = { id: 'lead', lead: true, title: 'The fixture claim' };
  const misses = [];
  const probe = (sections, columns, needle) => { saveOverview(p, { ...doc, columns, sections });
    const r = runNode([buildInDir]);
    const hit = needle ? r.code !== 0 && r.out.includes(needle) : r.code === 0;
    if (!hit) misses.push(`${needle || 'valid lead'} (exit=${r.code}) ${r.out.slice(0, 300)}`); };
  const se = doc.sections[0];
  probe([{ ...lead, order: 0 }, se], 1, null);
  probe([{ ...se, order: 1 }, { ...lead, order: 2 }], 1, "the page's FIRST band");
  probe([{ ...lead, order: 0 }, { ...se, span: 2 }], 2, 'write `span: 2`');
  probe([{ ...lead, order: 0, type: 'separator' }, se], 1, 'only a box can be the lead band');
  probe([{ ...se, children: [{ ...lead }, ...se.children.slice(1)] }], 1, "the page's FIRST band");
  report('LEAD: a first full-width root box builds; a later, narrower, nested or non-box lead is refused',
    misses.length === 0, misses.join('\n'));
  rmDeck(dir);
}
{
  const dir = mkDeck();
  const buildInDir = path.join(dir, 'engine', 'build-data.mjs');
  const { p, doc } = loadOverview(dir);
  findNode(doc, 'item-a').variant_extra = ['muted'];
  saveOverview(p, doc);
  const old = runNode([buildInDir]);
  delete findNode(doc, 'item-a').variant_extra;
  delete doc.layout;
  saveOverview(p, doc);
  const clean = runNode([buildInDir]);
  const ok = old.code === 0 && old.out.includes('[deprecated] `layout` on 1 page(s) (overview)')
    && old.out.includes('[deprecated] `variant_extra` on 1 component(s) (overview > item-a)')
    && clean.code === 0 && !clean.out.includes('[deprecated]');
  report('DEPRECATED: layout and variant_extra still build and warn by name; a deck without them is quiet', ok,
    `old exit=${old.code} clean exit=${clean.code}\n${old.out}\n${clean.out}`);
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
  // `--frame-h:40px;` is the ONLY default of --frame-h (every use is a bare
  // var()), so removing it leaves a stylesheet that still parses and renders
  // with its bundle, and no longer says what a deck without one draws.
  const probed = `--frame-h:${T.frame.h}px;`;
  const removed = src.includes(probed);
  fs.writeFileSync(idx, src.replace(probed, ''), 'utf8');
  const { code, out } = runNode([CHECK, dir]);
  const ok = removed && code !== 0
    && out.includes('declares no readable [') && out.includes('default of --frame-h')
    && !out.includes('ALL PASS');
  report('CSS/mirror: a deleted token default FAILS the gate', ok,
    `probe-present=${removed} exit=${code}\n${out}`);
  rmDeck(dir);
}
// The DEFAULTS contract, the other direction: a :root fallback that disagrees
// with DEFAULT_TOKENS fails by name, and so does a rule that stops spending its
// token through var() (the shape probe) — a literal clamp no longer moves with
// the data.
{
  const dir = mkDeck();
  const idx = path.join(dir, 'index.html');
  const src = fs.readFileSync(idx, 'utf8');
  const def = `--cell-h:${T.row.cell_h}px;`, shape = '-webkit-line-clamp:var(--desc-lines, 3)';
  fs.writeFileSync(idx, src.replace(def, `--cell-h:${T.row.cell_h - 10}px;`), 'utf8');
  const drifted = runNode([CHECK, dir]);
  fs.writeFileSync(idx, src.replace(shape, '-webkit-line-clamp:3'), 'utf8');
  const literal = runNode([CHECK, dir]);
  const ok = src.includes(def) && src.includes(shape)
    && drifted.code !== 0 && drifted.out.includes(`--cell-h: stylesheet default ${T.row.cell_h - 10}px vs DEFAULT_TOKENS ${T.row.cell_h}px`)
    && literal.code !== 0 && literal.out.includes('.box .desc clamps to var(--desc-lines)');
  report('CSS/defaults: a drifted :root default and a literal clamp both FAIL the gate', ok,
    `drift exit=${drifted.code} literal exit=${literal.code}\n${drifted.out.slice(-600)}\n${literal.out.slice(-600)}`);
  rmDeck(dir);
}

// ── 11. TOKENS — the data is the only input ────────────────────────────────
// THREE-LAYER AGREEMENT. One edit to document.yaml (row.cell_h 130 -> 110,
// type.desc.lines 3 -> 2) must move the engine's CSS properties, the static
// gate's model and the render gate's expectations together; each layer is
// asserted by what it DOES with the value, and the case fails on the first one
// that ignores it.
{
  const dir = mkDeck();
  const three = ['first line', 'second line', 'third line'];
  const { p, doc } = loadOverview(dir);
  findNode(doc, 'item-a').description = three;
  saveOverview(p, doc);
  rebuild(dir);
  const before = runNode([CHECK, dir]);
  writeDocument(dir, { tokens: { row: { cell_h: 110 }, type: { desc: { lines: 2 } } } });
  rebuild(dir);
  const after = runNode([CHECK, dir]);
  const gen = require(path.join(ROOT, 'tools', 'static-census.cjs')).loadGenerated(dir).doc;
  // engine: the bundle's :root projection, and the engine applies exactly it.
  const engineOk = gen.css_vars['--cell-h'] === '110px' && gen.css_vars['--desc-lines'] === '2'
    && ENGINE_SRC.includes('applyVars(document.documentElement, doc.css_vars)');
  // static gate: the model prints the moved values and three lines now overflow.
  const failDesc = '[FAIL] overview:root > section-e > item-a @1920px: description needs 3';
  const staticOk = !before.out.includes(failDesc) && after.out.includes('row 110px')
    && after.out.includes('desc 12px/2ln') && after.out.includes(failDesc);
  // render gate: its expectations follow, and U holds a 110px grid to them.
  const rx = VALIDATE.renderExpectations(gen.tokens);
  const U = VALIDATE.INVARIANTS.find(inv => inv.id === 'U' && inv.name === 'uniform slot height');
  const mFor = h => ({ heights: [h], halfSlots: [],
    rowTracks: [{ zone: 'fixture', tracks: [h], cellH: h, rows: [{ n: 1, sepH: 0, hole: 0, railH: 0 }], overflow: [] }] });
  VALIDATE.applyTokens(gen.tokens);
  const renderOk = rx.CELL_H === 110 && U.check(mFor(110)).ok === true && U.check(mFor(T.row.cell_h)).ok === false;
  VALIDATE.applyTokens(T);
  report('TOKENS/agree: cell_h 110 + desc.lines 2 move the engine vars, the static model and the render expectations',
    engineOk && staticOk && renderOk,
    `engine=${engineOk} static=${staticOk} render=${renderOk}\n${after.out.split('\n').filter(l => /TOKENS|row \d+px|item-a/.test(l)).join('\n')}`);
  rmDeck(dir);
}

// RANGE. An out-of-range token, a value under the legibility floor, a typo and
// a deck-wide key overridden on a section are each refused by the build, by name.
{
  const dir = mkDeck();
  const buildInDir = path.join(dir, 'engine', 'build-data.mjs');
  const refuse = (extra, needle) => { writeDocument(dir, extra); const r = runNode([buildInDir]);
    return r.code !== 0 && r.out.includes(needle) ? null : `${needle} (exit=${r.code}) ${r.out.slice(0, 200)}`; };
  const misses = [
    refuse({ tokens: { row: { cell_h: 20 } } }, 'tokens.row.cell_h must be an integer 60..400 px'),
    refuse({ tokens: { type: { desc: { px: 8 } } } }, 'tokens.type.desc.px must be a number 10..24 px'),
    refuse({ tokens: { row: { cell_hh: 120 } } }, 'did you mean "row.cell_h"?'),
    refuse({ tokens: { breakpoints: { two: 1500 } } }, 'one < two < stack'),
  ];
  writeDocument(dir, {});
  const { p, doc } = loadOverview(dir);
  findNode(doc, 'section-e').tokens = { type: { desc: { px: 14 } } };
  saveOverview(p, doc);
  const node = runNode([buildInDir]);
  if (!(node.code !== 0 && node.out.includes('tokens.type.desc.px is deck-wide and cannot be overridden here')))
    misses.push(`section override (exit=${node.code}) ${node.out.slice(0, 200)}`);
  report('TOKENS/range: out-of-range, sub-legible, misspelled and non-overridable tokens are refused',
    misses.every(m => m === null), misses.filter(Boolean).join('\n'));
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
