// test-guards.mjs — permanent NEGATIVE-test suite for the diagram-builder
// guards (check-layout.mjs, build-data.mjs strict-schema, the YAML reader and
// the engine's video hook). `model` is the mandatory gate for every deck edit; this
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
import { widthAtTier, isBandAtTier, isBandClass, place,
  textBudget, capacityFor, isThinRowLeaf, inkBudget,
  railTitleFit, railTitleWidth, headerBudget, predictPageHeight, pageHeightAdvisory,
  CSS_TEXT } from './check-layout.mjs';
import { DEFAULT_TOKENS, TOKEN_SCHEMA, LOOKS, getPath } from '../engine/tokens.mjs';
import { buildPlan, loadTimeline } from './video/timeline.mjs';

const require = createRequire(import.meta.url);
const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const CHECK = path.join(ROOT, 'tools', 'check-layout.mjs');
const BUILD = path.join(ROOT, 'engine', 'build-data.mjs');
const TOKENS_MODULE = path.join(ROOT, 'engine', 'tokens.mjs');
const INDEX = path.join(ROOT, 'index.html');
// Every fixture number below derives from the defaults, so a moved default moves
// the fixtures with it.
const T = DEFAULT_TOKENS;
const yaml = require(path.join(ROOT, 'engine', 'yaml.cjs'));
// Fixtures are written as JSON: one line of it is a flow mapping of the dialect.
const dumpYaml = data => JSON.stringify(data);

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
    dumpYaml(FIXTURE_DOCUMENT),
    'utf8',
  );
  fs.writeFileSync(
    path.join(dir, 'data', 'pages', 'overview.yaml'),
    dumpYaml(FIXTURE_OVERVIEW),
    'utf8',
  );
  fs.mkdirSync(path.join(dir, 'engine'));
  fs.mkdirSync(path.join(dir, 'tools'));
  fs.copyFileSync(BUILD, path.join(dir, 'engine', 'build-data.mjs'));
  fs.copyFileSync(TOKENS_MODULE, path.join(dir, 'engine', 'tokens.mjs'));
  for (const f of ['chips.cjs', 'yaml.cjs'])
    fs.copyFileSync(path.join(ROOT, 'engine', f), path.join(dir, 'engine', f));
  fs.copyFileSync(path.join(ROOT, 'tools', 'static-census.cjs'), path.join(dir, 'tools', 'static-census.cjs'));
  // The real stylesheet, so the CSS MIRROR is actually asserted in every case.
  // Without it every fixture here ran with the mirror unread — the guard quiet in
  // the whole suite whose reason for existing is that a quiet guard is the silent
  // false negative. Case 9 removes it again, deliberately, to assert that state.
  fs.copyFileSync(INDEX, path.join(dir, 'index.html'));
  execFileSync('node', [path.join(dir, 'engine', 'build-data.mjs')], {
    cwd: dir,
    stdio: 'ignore',
  });
  return dir;
}
function loadOverview(dir) {
  const p = path.join(dir, 'data', 'pages', 'overview.yaml');
  return { p, doc: yaml.parse(fs.readFileSync(p, 'utf8'), p) };
}
function saveOverview(p, doc) { fs.writeFileSync(p, dumpYaml(doc), 'utf8'); }
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

// ── 2b. FORM — the model refuses a form outside FORMS, as the build does ────
{
  const dir = mkDeck();
  const { p, doc } = loadOverview(dir);
  doc.form = 'dashboards';
  saveOverview(p, doc);
  const { code, out } = runNode([CHECK, dir]);
  const ok = code !== 0 && out.includes('form "dashboards" is not one of');
  report('FORM/model: unknown page form "dashboards" fails the model', ok, `exit=${code}`);
  rmDeck(dir);
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

// ── 3g2. CASCADE — the breakpoints link before the inline <style> FAILS ─────
// The generated collapse rules repeat inline selectors at equal specificity, so
// a tier applies only when its sheet loads later. Put the link back where it sat
// when stacked zones rendered squashed: the gate must name the link and the base
// rule that outranked the stack tier.
{
  const dir = mkDeck();
  const idx = path.join(dir, 'index.html');
  const src = fs.readFileSync(idx, 'utf8');
  const link = src.match(/<link\b[^>]*breakpoints\.generated\.css[^>]*>\n/)?.[0];
  if (link) fs.writeFileSync(idx, src.replace(link, '').replace('<style>', `${link}<style>`), 'utf8');
  const { code, out } = runNode([CHECK, dir]);
  const ok = !!link && code !== 0 && out.includes('CASCADE') && out.includes('before the last </style>')
    && out.includes('.sec-grid.sec-compound > .zone: { flex: var(--span, 1) 1 0; }');
  report('CASCADE: the breakpoints sheet linked before the inline <style> fails the static gate', ok,
    `link-found=${!!link} exit=${code}\n${out}`);
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

// ── 3j. INK/boundary — a box that needs exactly its row does not overflow ────
// A kicker, a two-line title at 17px and a three-line description need
// 13.125 + 43.5 + 50.4 + 16 + 3 + 4 = 130.025px: the report reads 130.0px of a
// 130.0px row, so that is equality, and one pixel more (17.4px title) is not.
{
  const leaf = { id: 'full', kicker: 'K', title: 'A title long enough to wrap onto two lines',
    description: ['one', 'two', 'three'] };
  const equal = inkBudget(leaf, { availPx: 200, fontPx: 17 });
  const over = inkBudget(leaf, { availPx: 200, fontPx: 17.4 });
  const line = ig => `need ${ig.ink.toFixed(1)}px and the slot is ${ig.slot.toFixed(1)}px — ` +
    `${(-ig.slack).toFixed(1)}px overflows`;
  const ok = equal.ink.toFixed(1) === '130.0' && equal.slot === 130 && equal.slack >= 0
    && over.ink.toFixed(1) === '131.0' && over.slot === 130 && (-over.slack).toFixed(1) === '1.0';
  report('INK/boundary: 130 needed of 130 available is no overflow, 131 of 130 overflows by 1.0px', ok,
    `equal: ${line(equal)} (slack ${equal.slack}) | one more: ${line(over)} (slack ${over.slack})`);
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

// ── 6/7. TEXT — the character budget points in the right DIRECTION ─────────
// The budget is the one APPROXIMATE check in the model, so what earns it its
// line is direction: a title token wider than its cell is flagged, and a title
// that fits is not flagged at all. An advisory that flags everything teaches its
// reader to ignore it. Both halves use ONE cell width and font size.
const N_CELL_PX = 270.5;   // a span-1 cell of the seed's 4-track, 1246px band grid
const N_FONT_PX = T.type.title.max_px;   // .box .t at the two widest tiers

const staticFlags = word => textBudget({ id: 'x', title: word },
  { availPx: N_CELL_PX, fontPx: N_FONT_PX, half: false, cell: 'cell' })
  .findings.some(f => f.kind === 'token');

// 6 — a token that FAILS N is flagged by the budget, end to end and by predicate.
{
  const LONG = 'Orquestacionmultiregionconsolidada';   // 34 chars, one token
  const cap = capacityFor(N_CELL_PX, N_FONT_PX);
  const budgetFlags = staticFlags(LONG);

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

  report('TEXT/agree: a token wider than its cell is flagged (advisory) by the static budget',
    budgetFlags === true && gateSpeaks,
    `budget=${budgetFlags} gate=${gateSpeaks} (${LONG.length} chars vs cap ${cap} @${N_CELL_PX}px) line=${line.trim().slice(0, 120)}`);
}

// 7 — TEETH IN THE OTHER DIRECTION: a token that fits must be flagged by NEITHER.
// Without this the budget could "agree" with N by flagging everything.
{
  const SHORT = 'Orden';                               // 5 chars, comfortably inside
  const cap = capacityFor(N_CELL_PX, N_FONT_PX);
  const budgetFlags = staticFlags(SHORT);

  const dir = mkDeck();
  const { p, doc } = loadOverview(dir);
  findNode(doc, 'item-b').title = SHORT;
  saveOverview(p, doc);
  const { out } = runNode([CHECK, dir]);
  const quiet = !out.includes(`title token "${SHORT}"`);
  rmDeck(dir);

  report('TEXT/teeth: a title that fits is not flagged',
    budgetFlags === false && quiet,
    `budget=${budgetFlags} quiet=${quiet} (${SHORT.length} chars vs cap ${cap} @${N_CELL_PX}px)`);
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
  dumpYaml({ ...FIXTURE_DOCUMENT, ...extra }), 'utf8');
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

// ── 10c. LOOK — one line picks the palette and the tokens, above the floors ──
// Every named look builds, carries its palette, and resolves to tokens at or
// above every schema minimum. A look beside a `palette` is refused. A test look
// injected into the fixture's LOOKS proves the floors bite on a look: one below
// a type floor is refused by the build, one that squeezes the plane fails model's
// LEGIBLE, and the same injection at the default plane passes (the teeth case:
// the red comes from the squeeze, not from the injection).
{
  const dir = mkDeck();
  const writeLook = (look, extra = {}) => fs.writeFileSync(path.join(dir, 'data', 'document.yaml'),
    dumpYaml({ ...FIXTURE_DOCUMENT, look, ...extra }), 'utf8');
  const bundle = () => {
    const src = fs.readFileSync(path.join(dir, 'data', 'data.generated.js'), 'utf8');
    const start = src.indexOf('window.__DOC__ = ') + 'window.__DOC__ = '.length;
    return JSON.parse(src.slice(start, src.indexOf(';\nif (', start)));
  };
  const buildInDir = path.join(dir, 'engine', 'build-data.mjs');
  const problems = [];
  for (const [name, look] of Object.entries(LOOKS)) {
    writeLook(name);
    const built = runNode([buildInDir]);
    if (built.code !== 0) { problems.push(`look ${name}: build exit ${built.code}\n${built.out}`); continue; }
    const doc = bundle();
    if (doc.look !== name || doc.palette !== look.palette)
      problems.push(`look ${name}: bundle carries look ${doc.look}, palette ${doc.palette}`);
    for (const [p, s] of Object.entries(TOKEN_SCHEMA)) {
      const v = getPath(doc.tokens, p);
      if (typeof s.min === 'number' && typeof v === 'number' && v < s.min)
        problems.push(`look ${name}: tokens.${p} ${v} is below its floor ${s.min}`);
    }
  }
  writeLook('brand', { palette: 'neutral' });
  const both = runNode([buildInDir]);
  const tokensFile = path.join(dir, 'engine', 'tokens.mjs');
  const shipped = fs.readFileSync(tokensFile, 'utf8');
  const anchor = "  brand: { palette: 'rose-pine', tokens: {} },";
  const injectTestLook = tokens => {
    fs.writeFileSync(tokensFile, shipped.replace(anchor, `${anchor}\n  test: { palette: 'neutral', tokens: ${tokens} },`), 'utf8');
    writeLook('test');
    const build = runNode([buildInDir]);
    return { build, model: build.code === 0 ? runNode([CHECK, dir]) : null };
  };
  // section-e is one zone of 4 tracks: 34px of zone chrome and 3 gaps of 8px.
  // At plane_max 640 a track is (640 - 34 - 24) / 4 ≈ 146px, under a 200px
  // floor at the 1200, 1920 and 2560 tiers; at 1280 it is ≈ 306px (≈ 258px at
  // the 1200 tier), and the collapsed tiers are wider still. The fixture's
  // 4 × 2 rectangle has no room for more tracks, so the look raises the floor
  // instead of adding columns, and plane_max alone separates red from control.
  const tiny = injectTestLook('{ type: { desc: { px: 8 } } }');
  const squeezed = injectTestLook('{ plane_max: 640, cell_min_w: 200 }');
  const control = injectTestLook('{ plane_max: 1280, cell_min_w: 200 }');
  // A finding line starts with [FAIL]; the closing summary also names "[FAIL] lines".
  const failLines = run => (run.model?.out ?? '').split('\n').filter(l => l.trimStart().startsWith('[FAIL]'));
  const squeezedFails = failLines(squeezed);
  const ok = problems.length === 0 && shipped.includes(anchor)
    && both.code !== 0 && both.out.includes('`look: brand` already chooses the palette')
    && tiny.build.code !== 0 && tiny.build.out.includes('look: tokens.type.desc.px must be a number 10..24 px')
    && squeezed.build.code === 0 && squeezed.model.code !== 0 && squeezedFails.length > 0
    && squeezedFails.every(l => l.includes('below the 200px legible floor (cell_min_w)'))
    && control.build.code === 0 && control.model.code === 0;
  report('LOOK: each look builds above the floors; look+palette, a sub-floor look and a squeezing look are refused', ok,
    `${problems.join('\n')}\nboth exit=${both.code} tiny exit=${tiny.build.code} ` +
    `squeezed build=${squeezed.build.code} model=${squeezed.model?.code} control model=${control.model?.code}\n` +
    `${both.out}\n${tiny.build.out}\n--- squeezed model ---\n${squeezed.model?.out ?? squeezed.build.out}\n` +
    `--- control model ---\n${control.model?.out ?? control.build.out}`);
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
  fs.writeFileSync(path.join(dir, 'data', 'pages', 'second.yaml'), dumpYaml({
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
// TWO-LAYER AGREEMENT. One edit to document.yaml (row.cell_h 130 -> 110,
// type.desc.lines 3 -> 2) must move the engine's CSS properties and the static
// gate's model together; each layer is
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
  report('TOKENS/agree: cell_h 110 + desc.lines 2 move the engine vars and the static model',
    engineOk && staticOk,
    `engine=${engineOk} static=${staticOk}\n${after.out.split('\n').filter(l => /TOKENS|row \d+px|item-a/.test(l)).join('\n')}`);
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

// ── 13. PALETTE OVERRIDES — built, emitted, audited from the generated data ──
// A legible override passes the contrast audit and reaches the generated CSS; a
// muted token washed out to near-white fails it even on `neutral`, which gates
// nothing of its own; a misspelled key, a non-colour value and an unknown theme
// are refused by the build.
{
  const dir = mkDeck();
  fs.copyFileSync(path.join(ROOT, 'tools', 'contrast-audit.cjs'), path.join(dir, 'tools', 'contrast-audit.cjs'));
  const audit = () => runNode([path.join(dir, 'tools', 'contrast-audit.cjs')]);
  const build = () => runNode([path.join(dir, 'engine', 'build-data.mjs')]);
  const misses = [];
  writeDocument(dir, { palette_overrides: { light: { ink: '#000000' } } });
  const goodBuild = build();
  const gen = fs.readFileSync(path.join(dir, 'data', 'data.generated.js'), 'utf8');
  const good = audit();
  if (goodBuild.code !== 0 || !gen.includes('html:not(.dark)[data-palette]:root { --ink:#000000; }')
      || good.code !== 0 || !good.out.includes('neutral · light + palette_overrides (--ink)'))
    misses.push(`legible override (build=${goodBuild.code} audit=${good.code}) ${good.out.slice(-400)}`);
  writeDocument(dir, { palette_overrides: { light: { muted: '#eeeeee' } } });
  build();
  const bad = audit();
  if (bad.code === 0 || !bad.out.includes('neutral/light+overrides muted-on-surface'))
    misses.push(`washed-out override (exit=${bad.code}) ${bad.out.slice(-400)}`);
  for (const [overrides, needle] of [
    [{ light: { 'hue-blu': '#123456' } }, 'did you mean "hue-blue"'],
    [{ dark: { ink: 'blue' } }, 'is not a colour'],
    [{ dusk: { ink: '#000' } }, 'unknown theme "dusk"'],
  ]) {
    writeDocument(dir, { palette_overrides: overrides });
    const r = build();
    if (r.code === 0 || !r.out.includes(needle)) misses.push(`${needle} (exit=${r.code}) ${r.out.slice(0, 300)}`);
  }
  report('PALETTE/overrides: a legible override ships and passes, a contrast miss fails, bad keys are refused',
    misses.length === 0, misses.join('\n'));
  rmDeck(dir);
}

// ── 14. YAML — the reader reads the dialect and refuses the rest by line ────
{
  const src = [
    '# a comment', 'title: "Deck — v1"   # trailing', 'n: 3', 'f: 1.5', 'on: true', 'none:', 'word: no',
    'list:', '  - plain text', '  - { id: a, kicker: "STEP 1 →", tags: [x, y] }',
    '  - id: b', '    description: ["one",', '      "two"]', 'nested:', "  deep: 'it''s'",
  ].join('\n');
  const want = { title: 'Deck — v1', n: 3, f: 1.5, on: true, none: null, word: 'no',
    list: ['plain text', { id: 'a', kicker: 'STEP 1 →', tags: ['x', 'y'] }, { id: 'b', description: ['one', 'two'] }],
    nested: { deep: "it's" } };
  let got;
  try { got = JSON.stringify(yaml.parse(src, 't')); } catch (e) { got = e.message; }
  const rejects = [['a: &x 1', 1], ['a: *x', 1], ['a:\n\tb: 1', 2], ['a: |\n  text', 1], ['a: 1\na: 2', 2],
    ['a: b: c', 1], ['a: 2024-01-01', 1], ['a:\n  - x\n   y', 3], ['---\na: 1', 1]];
  const silent = rejects.filter(([text, line]) => {
    try { yaml.parse(text, 't'); return true; }
    catch (e) { return !(e instanceof yaml.YamlError && e.message.startsWith(`t:${line}: `)); }
  });
  report('YAML: the dialect reads as js-yaml would; anchors, aliases, tabs, block scalars, duplicates, ' +
    'inline mappings, timestamps, multi-line plain scalars and document markers are refused by line',
    got === JSON.stringify(want) && silent.length === 0,
    `parsed=${got} | not refused by line: ${JSON.stringify(silent)}`);
}

// ── 15. VIDEO — the capture handle exists under ?video, and only there ──────
// The engine runs on the seed's generated data against inert stand-ins for the
// browser: every DOM object is a callable proxy that absorbs reads, writes and
// calls, so the real IIFE mounts every page and reaches its final wiring.
function inert() {
  const self = new Proxy(function () {}, {
    get: (_, k) => (k === Symbol.toPrimitive ? () => '' : k === Symbol.iterator ? function* () {}
      : k === 'length' ? 0 : k === 'then' ? undefined : self),
    set: () => true,
    apply: () => self,
    construct: () => self,
  });
  return self;
}
function runEngine(search) {
  const win = { location: { search } };
  const windowProxy = new Proxy(win, {
    get: (t, k) => (k in t ? t[k] : inert()),
    set: (t, k, v) => { t[k] = v; return true; },
  });
  const noTimer = () => 0;
  const own = { window: windowProxy, location: win.location, document: inert(), navigator: inert(),
    localStorage: inert(), setTimeout: noTimer, setInterval: noTimer, requestAnimationFrame: noTimer,
    queueMicrotask: noTimer };
  // `with` resolves every name the engine does not declare: ours first, then a
  // real global (Math, JSON, URLSearchParams…), then an inert browser object.
  const scope = new Proxy(own, {
    has: (t, k) => k in t || !(k in globalThis),
    get: (t, k) => (k in t ? t[k] : k === Symbol.unscopables ? undefined : inert()),
  });
  const run = code => new Function('scope', `with (scope) {\n${code}\n}`)(scope);
  run(fs.readFileSync(path.join(ROOT, 'data', 'data.generated.js'), 'utf8'));
  run(ENGINE_SRC);
  return win;
}
{
  let plain, video, driven = false;
  try {
    plain = runEngine('');
    video = runEngine('?video');
    const d = video.__deck;
    d.show(1); d.setFlow(0, 'all'); d.closePanel(0);
    driven = true;
  } catch (e) { driven = e.message; }
  const deck = video && video.__deck;
  const ok = !!plain && !('__deck' in plain) && !!deck && Array.isArray(deck.acts) && deck.acts.length > 0
    && ['show', 'setFlow', 'closePanel'].every(k => typeof deck[k] === 'function') && driven === true;
  report('VIDEO: ?video exposes window.__deck {acts, show, setFlow, closePanel} driving the wired acts; without it, none',
    ok, `plain __deck=${plain ? '__deck' in plain : 'n/a'} | video __deck=${deck ? Object.keys(deck).join(',') : 'none'} | driven=${driven}`);
}

// ── 16. CENSUS — the width the grid GIVES a node, not the one authored ──────
// section-e authors 8 columns but its content fills 6 (six single cells and one
// span-2), so a census that echoed the YAML would report 8 and "1/4" (2 of 8)
// instead of "1/3" (2 of 6).
{
  const dir = mkDeck();
  const name = 'CENSUS: reports resolved columns/width, the variant in use and the chip members by authored id';
  try {
    const { p, doc } = loadOverview(dir);
    findNode(doc, 'section-e').columns = 8;
    findNode(doc, 'item-a').variant = 'blue';
    saveOverview(p, doc);
    rebuild(dir);
    const { code, out } = runNode([path.join(ROOT, 'tools', 'census.mjs'), dir, '--json']);
    const page = JSON.parse(out).pages[0];
    const section = page.sections[0];
    const got = { code, section: section.id, columns: section.columns,
      width: section.children.find(c => c.id === 'item-c').width,
      blue: page.variants.blue, flow: page.chips.find(c => c.key === 'flow').members };
    const ok = code === 0 && got.section === 'section-e'
      && got.columns.authored === 8 && got.columns.effective === 6 && got.width === '1/3'
      && JSON.stringify(got.blue) === '["item-a"]' && got.flow.join(',') === 'item-1,item-3,item-7';
    report(name, ok, JSON.stringify(got));
  } catch (e) {
    report(name, false, e.message);
  } finally {
    rmDeck(dir);
  }
}

// ── 17. CENSUS — read against a sketch, the JSON alone must be unambiguous ─
// `lanes` is a columns:1 compound: its groups STACK (`.sec-c1` is a column), so
// they start on two rows at 1/1. `pair` is a columns:2 row: its groups sit side
// by side on one row at 1/2. A census that reported both pairs "full" in a "row"
// could not tell the two apart. `anchor` is the forward-filling control: the
// rowspan-2 cell pushes under-1 to row 2, column 2.
{
  const dir = mkDeck();
  const name = 'CENSUS: start per node, one width form, stacked vs side by side, variant source, chip scope';
  try {
    const { p, doc } = loadOverview(dir);
    const box = id => ({ id, title: id });
    const group = (id, ...ids) => ({ id, columns: 1, children: ids.map(box) });
    doc.columns = 2;
    doc.filters.push({ key: 'all', label: 'All' });
    findNode(doc, 'section-e').span = 2;
    doc.sections.push(
      { id: 'lanes', span: 1, columns: 1, children: [group('lane-1', 'l1'), group('lane-2', 'l2', 'l3')] },
      { id: 'pair', span: 1, columns: 2, children: [group('side-1', 's1'), group('side-2', 's2')] },
      { id: 'anchor', span: 2, columns: 3, children: [
        { id: 'a-anchor', title: 'Tall', rowspan: 2 }, box('beside-1'), box('beside-2'),
        box('under-1'), box('under-2'), { id: 'a-sep', type: 'separator', span: 3 },
        { id: 'a-rail', type: 'rail', title: 'Rail' }, box('a-2'), box('a-3')] });
    saveOverview(p, doc);
    rebuild(dir);
    const { code, out } = runNode([path.join(ROOT, 'tools', 'census.mjs'), dir, '--json']);
    const page = JSON.parse(out).pages[0];
    const nodes = [];
    (function walk(list) { for (const n of list) { nodes.push(n); walk(n.children || []); } })(page.sections);
    const byId = Object.fromEntries(nodes.map(n => [n.id, n]));
    const gcd = (a, b) => (b ? gcd(b, a % b) : a);
    const normal = w => w === 'content' || (/^(\d+)\/(\d+)$/.test(w) && gcd(...w.split('/').map(Number)) === 1);
    const at = id => byId[id] && byId[id].start;
    const bad = [];
    const [l1, l2, s1, s2] = ['lane-1', 'lane-2', 'side-1', 'side-2'].map(id => byId[id] || {});
    if (!(at('lane-1')?.row === 1 && at('lane-2')?.row === 2 && l1.width === '1/1' && l2.width === '1/1'))
      bad.push(`lanes (columns:1) must stack: lane-1 ${JSON.stringify(at('lane-1'))} ${l1.width}, lane-2 ${JSON.stringify(at('lane-2'))} ${l2.width}, grid ${byId.lanes?.grid}`);
    if (!(at('side-1')?.row === at('side-2')?.row && at('side-1')?.col !== at('side-2')?.col
      && s1.width === '1/2' && s2.width === '1/2'))
      bad.push(`pair (columns:2) must sit side by side: side-1 ${JSON.stringify(at('side-1'))} ${s1.width}, side-2 ${JSON.stringify(at('side-2'))} ${s2.width}`);
    const unplaced = nodes.filter(n => !(Number.isInteger(n.start?.row) && Number.isInteger(n.start?.col))).map(n => n.id);
    if (unplaced.length) bad.push(`no start: ${unplaced.join(', ')}`);
    if (!(at('under-1')?.row === 2 && at('under-1')?.col === 2))
      bad.push(`under-1 must start at row 2 col 2 beside the rowspan-2 anchor: ${JSON.stringify(at('under-1'))}`);
    const offForm = nodes.filter(n => !normal(n.width)).map(n => `${n.id}=${n.width}`);
    if (offForm.length) bad.push(`width not in the one reduced form: ${offForm.join(', ')}`);
    const SOURCES = { authored: v => v != null, default: v => v === 'neutral', colourless: v => v == null };
    const unsaid = nodes.filter(n => !SOURCES[n.variant_source]?.(n.variant)).map(n => `${n.id}=${n.variant}/${n.variant_source}`);
    if (unsaid.length) bad.push(`variant without its source: ${unsaid.join(', ')}`);
    const chipScope = page.chips.filter(c => !c.members.length && !['all', 'none'].includes(c.scope));
    if (chipScope.length || page.chips.find(c => c.key === 'all')?.scope !== 'all')
      bad.push(`empty chip without a scope: ${JSON.stringify(page.chips.map(c => [c.key, c.scope]))}`);
    report(name, code === 0 && bad.length === 0, `exit ${code}; ${bad.join(' | ')}`);
  } catch (e) {
    report(name, false, e.message);
  } finally {
    rmDeck(dir);
  }
}

// ── 18. CATALOGUE — what the skill names, the seed shows, and nothing else ──
// The vocabulary is read from SKILL.md, never restated here: the bold terms of
// "What the person has", the bold terms of the map's Use and Neighbour columns,
// and the lowercase fields its Use column names in backticks (`rowspan`,
// `span`). An entry is a root section `piece-<slug>` on a visible `pieces-*`
// page, holding its live drawing and the three readings beside it; the
// neighbour its `NEIGHBOUR · <piece>` kicker names must be a piece too. A
// scaffolded deck ships without SKILL.md, so there the case is skipped, never
// passed.
const PIECE_PARTS = ['live', 'yaml', 'says', 'neighbour'];

function skillSection(md, heading) {
  const at = md.indexOf(`\n## ${heading}\n`);
  if (at < 0) throw new Error(`SKILL.md has no "## ${heading}" section`);
  const end = md.indexOf('\n## ', at + 1);
  return md.slice(at, end < 0 ? md.length : end);
}
function pieceTerm(raw) {
  return raw.replace(/`/g, '').split(' = ')[0].replace(/\.$/, '').replace(/^one /, '').trim().toLowerCase();
}
function skillVocabulary(md) {
  const bold = text => [...text.matchAll(/\*\*(.+?)\*\*/g)].map(m => pieceTerm(m[1]));
  const terms = new Set(bold(skillSection(md, 'What the person has')));
  const rows = skillSection(md, 'The map: from an idea to a piece').split('\n')
    .filter(line => line.startsWith('| "'));
  if (!rows.length) throw new Error('the map in SKILL.md has no rows');
  for (const row of rows) {
    const [, , use, , neighbour] = row.split('|');
    for (const term of [...bold(use), ...bold(neighbour)]) terms.add(term);
    for (const m of use.matchAll(/`([a-z]+)`/g)) terms.add(m[1]);
  }
  return terms;
}
function catalogueEntries(root) {
  const docFile = path.join(root, 'data', 'document.yaml');
  const manifest = yaml.parse(fs.readFileSync(docFile, 'utf8'), docFile);
  const entries = new Map();
  for (const entry of manifest.pages || []) {
    if (!entry.id.startsWith('pieces-') || entry.visible !== true) continue;
    const file = path.join(root, 'data', entry.file);
    const page = yaml.parse(fs.readFileSync(file, 'utf8'), file);
    for (const node of page.sections || []) {
      const m = /^piece-(.+)$/.exec(node.id || '');
      if (m) entries.set(m[1].replace(/-/g, ' '), { page: entry.id, node });
    }
  }
  return entries;
}
function catalogueDivergence(vocabulary, entries) {
  const bad = [];
  const unshown = [...vocabulary].filter(term => !entries.has(term));
  if (unshown.length) bad.push(`named by the skill, no live entry: ${unshown.join(', ')}`);
  const unnamed = [...entries.keys()].filter(term => !vocabulary.has(term));
  if (unnamed.length) bad.push(`shown, not named by the skill: ${unnamed.join(', ')}`);
  for (const [term, { page, node }] of entries) {
    const kickers = new Map();
    (function walk(n) { kickers.set(n.id, n.kicker); (n.children || []).forEach(walk); })(node);
    const missing = PIECE_PARTS.filter(part => !kickers.has(`${node.id}-${part}`));
    if (missing.length) { bad.push(`${page}/${node.id} lacks ${missing.join(', ')}`); continue; }
    const kicker = kickers.get(`${node.id}-neighbour`) || '';
    const said = /^NEIGHBOUR · (.+)$/.exec(kicker)?.[1].toLowerCase();
    if (!said || said === term || !vocabulary.has(said))
      bad.push(`${page}/${node.id} names no neighbour piece: "${kicker}"`);
  }
  return bad;
}
{
  const name = 'CATALOGUE: every piece SKILL.md names has a live entry, and no entry shows an unnamed piece';
  const skillFile = path.join(ROOT, '..', 'SKILL.md');
  if (!fs.existsSync(skillFile)) {
    console.log(`[SKIP] ${name} — no SKILL.md beside this deck (a scaffold, not the skill)`);
  } else {
    try {
      const vocabulary = skillVocabulary(fs.readFileSync(skillFile, 'utf8'));
      const entries = catalogueEntries(ROOT);
      const bad = catalogueDivergence(vocabulary, entries);
      report(name, bad.length === 0, `${vocabulary.size} named, ${entries.size} shown; ${bad.join(' | ')}`);
      const seeded = new Map(entries);
      seeded.delete([...vocabulary][0]);
      seeded.set('arrow', { page: 'seeded', node: { id: 'piece-arrow', children: [] } });
      const caught = catalogueDivergence(vocabulary, seeded);
      report('CATALOGUE/teeth: a missing entry and an unnamed piece are both reported',
        caught.some(b => b.startsWith('named by the skill')) && caught.some(b => b.startsWith('shown, not named')),
        caught.join(' | ') || 'the comparator accepted a seeded divergence');
    } catch (e) {
      report(name, false, e.message);
    }
  }
}

// ── 19. PATH — the seed is a tour: story → ideas → pieces → data ──
// Each visible page of the seed declares the step it teaches as the prefix of
// its manifest `name` ("Ideas · …"), so the step reads in the page tabs, and
// along `order` the steps only move forward. The pieces step is exactly the
// catalogue's `pieces-*` pages, which keeps the catalogue one block. A
// scaffolded deck is the person's own story, not the tour, so there the case
// is skipped, never passed.
const PATH_STEPS = ['Story', 'Ideas', 'Pieces', 'Data'];

function pathDivergence(pages) {
  const bad = [];
  let reached = 0;
  let reachedBy = '(the start)';
  const tour = pages.filter(p => p.visible === true).sort((a, b) => a.order - b.order);
  for (const entry of tour) {
    const step = PATH_STEPS.indexOf(/^(\w+) · /.exec(entry.name || '')?.[1]);
    if (step < 0) { bad.push(`${entry.id} declares no step: "${entry.name}"`); continue; }
    if ((PATH_STEPS[step] === 'Pieces') !== entry.id.startsWith('pieces-'))
      bad.push(`${entry.id} declares ${PATH_STEPS[step]}; the pieces step is exactly the pieces-* pages`);
    if (step < reached) bad.push(`${entry.id} (${PATH_STEPS[step]}) comes after ${reachedBy} (${PATH_STEPS[reached]})`);
    else { reached = step; reachedBy = entry.id; }
  }
  return bad;
}
{
  const name = 'PATH: every seed page declares its step, and the steps run story → ideas → pieces → data';
  if (!fs.existsSync(path.join(ROOT, '..', 'SKILL.md'))) {
    console.log(`[SKIP] ${name} — no SKILL.md beside this deck (a scaffold, not the skill)`);
  } else {
    try {
      const docFile = path.join(ROOT, 'data', 'document.yaml');
      const pages = yaml.parse(fs.readFileSync(docFile, 'utf8'), docFile).pages || [];
      const bad = pathDivergence(pages);
      report(name, bad.length === 0, bad.join(' | ') || `${pages.length} pages in path order`);
      const caught = pathDivergence([
        { id: 'seeded-ideas', name: 'Ideas · grouped', order: 1, visible: true },
        { id: 'seeded-story', name: 'Story · told late', order: 2, visible: true },
        { id: 'seeded-bare', name: 'No step here', order: 3, visible: true },
      ]);
      report('PATH/teeth: an undeclared step and a step out of order are both reported',
        caught.some(b => b.includes('declares no step')) && caught.some(b => b.includes('comes after')),
        caught.join(' | ') || 'the comparator accepted a seeded divergence');
    } catch (e) {
      report(name, false, e.message);
    }
  }
}

// ── VIDEO PIPELINE — made from a deck only, Playwright from one place ───────
// tools/video is copied into each fixture WITHOUT its node_modules, so these
// cases hold whether or not Playwright is installed for the real deck.
const VIDEO_TOOLS = path.join(ROOT, 'tools', 'video');
const VIDEO_SCRIPTS = ['script', 'align', 'plan', 'check', 'contact', 'capture', 'split'];
const BROWSER_SCRIPTS = ['check', 'contact', 'capture'];
const FIXTURE_SCRIPT = { pages: [{ page: 'overview', audio: 'audio/overview.wav', sentences: [
  { say: 'This page has one section.', show: ['section-e'] },
  { say: 'The flow chip lights three of its cells.', chip: 'flow' }] }] };
function copyVideoTools(dir) {
  const to = path.join(dir, 'tools', 'video');
  fs.mkdirSync(to, { recursive: true });
  if (!fs.existsSync(VIDEO_TOOLS)) return;
  for (const f of fs.readdirSync(VIDEO_TOOLS)) {
    if (f !== 'node_modules') fs.copyFileSync(path.join(VIDEO_TOOLS, f), path.join(to, f));
  }
}
function mkVideoDeck(engineSrc = ENGINE_SRC, script = FIXTURE_SCRIPT) {
  const dir = mkDeck();
  fs.writeFileSync(path.join(dir, 'engine', 'engine.js'), engineSrc, 'utf8');
  copyVideoTools(dir);
  fs.mkdirSync(path.join(dir, 'video'));
  fs.writeFileSync(path.join(dir, 'video', 'script.json'), JSON.stringify(script), 'utf8');
  return dir;
}
const runVideo = (dir, name, ...args) => runNode([path.join(dir, 'tools', 'video', `${name}.mjs`), ...args]);
const excerpt = out => JSON.stringify(out.trim().slice(0, 300));

{
  const name = 'VIDEO: every pipeline script refuses a tree with no deck, and a deck with no ?video hook';
  const bare = fs.mkdtempSync(path.join(os.tmpdir(), 'diagram-guard-'));
  copyVideoTools(bare);
  const hookless = mkVideoDeck(ENGINE_SRC.replace(/window\.__deck\s*=/, 'window.__noDeck ='));
  try {
    const bad = [];
    for (const s of VIDEO_SCRIPTS) {
      const a = runVideo(bare, s);
      if (a.code === 0 || !/no deck/.test(a.out)) bad.push(`${s} with no deck: exit ${a.code} ${excerpt(a.out)}`);
      const b = runVideo(hookless, s);
      if (b.code === 0 || !/\?video hook/.test(b.out)) bad.push(`${s} with no hook: exit ${b.code} ${excerpt(b.out)}`);
    }
    report(name, bad.length === 0, bad.join(' | '));
  } finally {
    rmDeck(bare);
    rmDeck(hookless);
  }
}

{
  const name = 'VIDEO: with no Playwright in tools/video, check, contact and capture stop on one line naming the install, before any work';
  const dir = mkVideoDeck();
  try {
    const install = `npm install --prefix ${fs.realpathSync(path.join(dir, 'tools', 'video'))}`;
    const bad = [];
    for (const s of BROWSER_SCRIPTS) {
      const { code, out } = runVideo(dir, s, '--page', 'overview', '--at', '1');
      const ok = code !== 0 && out.trim().split('\n').length === 1 && /Playwright/.test(out)
        && out.includes(install) && !/ERR_MODULE_NOT_FOUND|\n\s+at /.test(out)
        && !fs.existsSync(path.join(dir, 'out'));
      if (!ok) bad.push(`${s}: exit ${code} ${excerpt(out)}`);
    }
    report(name, bad.length === 0, bad.join(' | '));
  } finally {
    rmDeck(dir);
  }
}

{
  const name = 'VIDEO: the script exports per page as only what is said, and a provider field in it is refused';
  const dir = mkVideoDeck();
  try {
    const exported = runVideo(dir, 'script');
    const file = path.join(dir, 'out', 'video', 'script', '01-overview.txt');
    const text = fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : null;
    const want = FIXTURE_SCRIPT.pages[0].sentences.map(s => s.say).join('\n') + '\n';
    const voiced = JSON.parse(JSON.stringify(FIXTURE_SCRIPT));
    voiced.pages[0].sentences[0].voice = 'am_michael';
    fs.writeFileSync(path.join(dir, 'video', 'script.json'), JSON.stringify(voiced), 'utf8');
    const refused = runVideo(dir, 'script');
    report(name, exported.code === 0 && text === want && refused.code !== 0 && /"voice"/.test(refused.out),
      `export exit ${exported.code} text=${JSON.stringify(text)} | voice field: exit ${refused.code} ${excerpt(refused.out)}`);
  } finally {
    rmDeck(dir);
  }
}

{
  const name = 'VIDEO: the timeline follows the deck — its order is kept, and a section shown before its parent is refused';
  const outOfOrder = JSON.parse(JSON.stringify(FIXTURE_SCRIPT));
  outOfOrder.pages[0].sentences = [
    { say: 'A cell first.', show: ['item-1'] },
    { say: 'Then the section that holds it.', show: ['section-e'] }];
  const good = mkVideoDeck();
  const bad = mkVideoDeck(ENGINE_SRC, outOfOrder);
  try {
    const planned = runVideo(good, 'plan');
    const refused = runVideo(bad, 'plan');
    report(name, planned.code === 0 && /overview/.test(planned.out)
      && refused.code !== 0 && /deck's order/.test(refused.out),
    `plan exit ${planned.code} ${excerpt(planned.out)} | out of order: exit ${refused.code} ${excerpt(refused.out)}`);
  } finally {
    rmDeck(good);
    rmDeck(bad);
  }
}

// ── VIDEO WORD CUES — a cue fires at its word, several chips per sentence ───
// The timeline is built in-process from the guard's own page and script, with
// word timings as align.json keeps them, so every expected second is known.
{
  const name = 'VIDEO/words: a word cue fires at its word, each chip of a sentence at its own word, ' +
    'reveals in YAML order, no ring and no rise; without word timings a word cue is estimated inside its sentence';
  const doc = { pages: [{ id: 'words', filters: [{ key: 'one' }, { key: 'two' }], sections: [
    { id: 'sec', children: [{ id: 'a', filters: ['one'] }, { id: 'b', filters: ['two'] }] }] }] };
  const said = [
    { say: 'The section opens here.', show: ['sec'] },
    { say: 'First comes a, then b.', cues: [{ at: 'comes', show: ['a'] }, { at: 'then', show: ['b'] }] },
    { say: 'One chip lights a and the other lights b.', cues: [{ at: 'chip', chip: 'one' }, { at: 'other', chip: 'two' }] }];
  const script = { pages: [{ page: 'words', audio: 'audio/words.wav', sentences: said }] };
  const timed = (text, start, words) => ({ text, start, end: words[words.length - 1][1] + 0.3,
    words: words.map(([word, t]) => ({ word, start: t })) });
  const sentences = [
    timed(said[0].say, 0, [['The', 0], ['section', 0.3], ['opens', 0.8], ['here', 1.2], ['.', 1.5]]),
    timed(said[1].say, 2, [['First', 2], ['comes', 2.4], ['a', 2.8], [',', 2.9], ['then', 3.2], ['b', 3.6]]),
    timed(said[2].say, 5, [['One', 5], ['chip', 5.3], ['lights', 5.6], ['a', 5.9], ['and', 6.1], ['the', 6.3],
      ['other', 6.5], ['lights', 6.8], ['b', 7.1]])];
  const words = { pages: [{ page: 'words', method: 'words', duration: 8, sentences }] };
  const silence = { pages: [{ page: 'words', method: 'silencedetect', duration: 8,
    sentences: sentences.map(({ words: _, ...s }) => s) }] };
  try {
    const plan = buildPlan(loadTimeline(doc, script), words);
    const p = plan.pages[0];
    const at = sec => +(p.voiceAt + sec).toFixed(3);
    const reveals = p.cues.filter(c => c.reveal).map(c => `${c.reveal.join('+')}@${+c.t.toFixed(3)}`);
    const chips = p.cues.filter(c => c.chip).map(c => `${c.chip}@${+c.t.toFixed(3)}`);
    const estimated = buildPlan(loadTimeline(doc, script), silence).pages[0].cues.filter(c => c.chip);
    const inside = estimated.every(c => c.estimated && c.t > p.voiceAt + 5 && c.t < p.voiceAt + 7.4)
      && estimated.length === 2 && estimated[0].t < estimated[1].t;
    const ok = reveals.join(' ') === `sec@${at(0)} a@${at(2.4)} b@${at(3.2)}`
      && chips.join(' ') === `one@${at(5.3)} two@${at(6.5)}`
      && !/"(rise|rings?)"\s*:/.test(JSON.stringify(plan)) && inside;
    report(name, ok, `reveals ${reveals.join(' ')} | chips ${chips.join(' ')} | motion ${JSON.stringify(plan.reveal)} | ` +
      `no word timings: ${estimated.map(c => `${c.chip}@${c.t.toFixed(3)} estimated=${c.estimated}`).join(' ')}`);
  } catch (e) {
    report(name, false, e.message);
  }
}

// ── VOICE BLEND — kokoro takes a comma-separated list of voices and a speed ──
// A stub interpreter stands in for the venv's python and records its argv, so
// the adapter's resolution is checked with no Kokoro installed.
{
  const name = 'VOICE: kokoro accepts a comma-separated blend, finds each voice\'s .pt, names a missing one, ' +
    'and passes the speed through';
  const dir = mkVideoDeck();
  const venv = path.join(dir, 'venv');
  const model = path.join(dir, 'model');
  const argsFile = path.join(dir, 'kokoro-args.txt');
  fs.mkdirSync(path.join(venv, 'bin'), { recursive: true });
  fs.mkdirSync(path.join(model, 'voices'), { recursive: true });
  fs.writeFileSync(path.join(model, 'config.json'), '{}');
  for (const v of ['am_michael', 'af_heart']) fs.writeFileSync(path.join(model, 'voices', `${v}.pt`), '');
  fs.writeFileSync(path.join(venv, 'bin', 'python'), `#!/bin/sh\nprintf '%s\\n' "$@" > '${argsFile}'\n`, { mode: 0o755 });
  try {
    const exported = runVideo(dir, 'script');
    const kokoroArgs = ['--provider', 'kokoro', '--kokoro-venv', venv, '--kokoro-model', model];
    const blend = runVideo(dir, 'voice', ...kokoroArgs, '--voice', 'am_michael,af_heart', '--speed', '1.1');
    const argv = fs.existsSync(argsFile) ? fs.readFileSync(argsFile, 'utf8').split('\n') : [];
    const after = flag => argv[argv.indexOf(flag) + 1];
    const missing = runVideo(dir, 'voice', ...kokoroArgs, '--voice', 'am_michael,af_nope');
    const ok = exported.code === 0 && blend.code === 0 && !/manual provider/.test(blend.out)
      && after('--voice') === 'am_michael,af_heart' && after('--speed') === '1.1'
      && /voices\/af_nope\.pt/.test(missing.out) && !/voices\/am_michael\.pt/.test(missing.out);
    report(name, ok, `blend: exit ${blend.code} ${excerpt(blend.out)} argv=${JSON.stringify(argv)} | ` +
      `missing member: ${excerpt(missing.out)}`);
  } finally {
    rmDeck(dir);
  }
}

console.log(`\n${failures === 0 ? 'OK' : 'FAILED'} — ${failures} guard(s) did not detect their defect.`);
process.exit(failures === 0 ? 0 : 1);
