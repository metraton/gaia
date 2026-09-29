// census.mjs — what each page IS, per page and in reading order, for a reader
// who holds the deck against the sketch agreed with the user: its sections and
// how they nest, the width the grid gives every node, the colours and the chips.
//
// Run: npm run census [-- --json] [-- <deckRoot>]
//
// Every id, variant and chip key is the one the YAML authors, so a difference
// from the sketch can be named. Widths and positions are RESOLVED, not authored:
// `columns` and `span` go through the model's own grow-with-content clamp, and
// `width` and `start` are what the grid gives the node at the presentation
// viewport (`tokens.viewport.w`), from the rules check-layout.mjs asserts:
//   width   the share of the parent's width, as ONE reduced fraction "a/b" —
//           "1/1" is the whole row, "1/2" half of it, "2/3" two thirds — or
//           "content" for a component sitting directly in a flex row, sized by
//           its content
//   start   {row, col}, 1-based within the parent: the cell a grid places the
//           node in, the line and position on it in a flex row, and the ordinal
//           row in a stack
// A section's `grid` says how its children are laid out: "tracks" (a leaf CSS
// grid), "root" (the page's section grid), "row" (a nested flex row) or "stack"
// (`columns: 1` — one child per row at every tier). "root" and "row" also stack
// at or below the stack breakpoint.
// `variant` is the colour in force: `variant_source` says whether it was
// "authored" or is the engine's "default" (neutral — the engine never inherits a
// colour from a section), and "colourless" for a separator or spacer. A chip's
// `scope` says what it lights: its "members", "all" (the reserved reset, which
// declares none) or "none" (a key nobody lists).
//
// The census reads the authored YAML and computes with the tokens of the last
// build; it exits non-zero when that build no longer matches the YAML, because a
// census of a deck nobody built would validate the wrong thing.
import path from 'node:path';
import censusLib from './static-census.cjs';
import { applyTokens, tracksFor, widthAtTier, slotsOf, effectiveCols, orderedChildren, place,
  rowShare, rowspanOf, isBandClass, RESET_CHIP } from './check-layout.mjs';
import { DEFAULT_TOKENS } from '../engine/tokens.mjs';

const { DEFAULT_ROOT, DEFAULT_FORM, loadAuthoredDeck, loadGenerated, staticCensus } = censusLib;

const isSection = n => !!(n && Array.isArray(n.children));
const kindOf = n => (isSection(n) ? 'section' : (n && n.type) || 'box');
const COLOURLESS = new Set(['separator', 'spacer']);

const gcd = (a, b) => (b ? gcd(b, a % b) : a);
const fraction = (a, b) => `${a / gcd(a, b)}/${b / gcd(a, b)}`;

// The grid a page root or a section lays its children out in.
function gridOf(node, children, isRoot) {
  const compound = children.some(isSection);
  const cols = effectiveCols(node.columns, slotsOf(children), compound);
  const spanOf = n => Math.max(1, Math.min(Number(n && n.span) || 1, cols));
  const kind = !compound ? 'tracks' : cols <= 1 ? 'stack' : isRoot ? 'root' : 'row';
  return { kind, isRoot, cols, children, spanOf, columns: { authored: node.columns ?? null, effective: cols } };
}

function widthIn(grid, child, viewportW) {
  if (grid.kind === 'tracks') {
    const tracks = tracksFor(grid.cols, viewportW);
    return fraction(widthAtTier(grid.spanOf(child), grid.cols, tracks), tracks);
  }
  const share = rowShare(grid, child, viewportW);
  return share ? fraction(share.span, share.total) : 'content';
}

// Where each child of `grid` starts, keyed by node. A leaf grid and a root
// holding a band are CSS grids, placed by the model's own auto-placement; any
// other compound is a flex row that breaks its line only around a band.
function startsIn(grid, viewportW, stackW) {
  const starts = new Map();
  const ordered = orderedChildren(grid.children).map(x => x.c);
  const rootGrid = grid.kind === 'root' && ordered.some(n => isBandClass(grid.spanOf(n), grid.cols));
  if (grid.kind === 'tracks' || (rootGrid && viewportW > stackW)) {
    const tracks = grid.kind === 'tracks' ? tracksFor(grid.cols, viewportW) : grid.cols;
    const slots = slotsOf(grid.children);
    const { placed } = place(slots.map(slot => ({ slot, h: grid.kind === 'tracks' ? rowspanOf(slot.node) : 1,
      w: widthAtTier(grid.spanOf(slot.node), grid.cols, tracks) })), tracks);
    for (const p of placed)
      for (const n of p.slot.pair || [p.slot.node]) starts.set(n, { row: p.r + 1, col: p.c + 1 });
    return starts;
  }
  if (grid.kind === 'stack' || viewportW <= stackW) {
    ordered.forEach((n, i) => starts.set(n, { row: i + 1, col: 1 }));
    return starts;
  }
  let row = 1, col = 0;
  for (const n of ordered) {
    if (isBandClass(grid.spanOf(n), grid.cols)) {
      if (col > 0) row++;
      starts.set(n, { row, col: 1 });
      row++; col = 0;
    } else {
      starts.set(n, { row, col: ++col });
    }
  }
  return starts;
}

function variantOf(node) {
  if (node.variant != null) return { variant: node.variant, variant_source: 'authored' };
  if (COLOURLESS.has(kindOf(node))) return { variant: null, variant_source: 'colourless' };
  return { variant: 'neutral', variant_source: 'default' };
}

function censusOfPage(entry, page, manifest, viewportW, stackW) {
  const variants = {};
  const chips = (page.filters || []).map(f => ({ key: f.key, label: f.label ?? null, core: false, members: [] }));
  const coreKeys = new Set((manifest.filters || []).map(f => f.key));
  for (const chip of chips) chip.core = coreKeys.has(chip.key);

  const describe = (node, grid, starts) => {
    const out = {
      id: node.id ?? null, kind: kindOf(node), title: node.title ?? null, ...variantOf(node),
      span: { authored: node.span ?? null, resolved: grid.spanOf(node), rows: node.rowspan ?? 1 },
      start: starts.get(node),
      width: widthIn(grid, node, viewportW),
    };
    if (Array.isArray(node.treatment) && node.treatment.length) out.treatment = node.treatment;
    if (out.variant != null) (variants[out.variant] ||= []).push(out.id);
    const keys = Array.isArray(node.filters) ? node.filters : [];
    if (!isSection(node)) out.chips = keys;
    for (const key of keys) chips.find(c => c.key === key)?.members.push(out.id);
    if (isSection(node)) {
      const inner = gridOf(node, node.children, false);
      Object.assign(out, { columns: inner.columns, grid: inner.kind });
      const innerStarts = startsIn(inner, viewportW, stackW);
      out.children = orderedChildren(node.children).map(({ c }) => describe(c, inner, innerStarts));
    }
    return out;
  };

  const root = gridOf(page, page.sections || [], true);
  const rootStarts = startsIn(root, viewportW, stackW);
  const sections = orderedChildren(page.sections).map(({ c }) => describe(c, root, rootStarts));
  for (const chip of chips)
    chip.scope = chip.members.length ? 'members' : chip.key === RESET_CHIP ? 'all' : 'none';
  return { id: String(entry.id), name: entry.name ?? null, order: entry.order ?? null,
    form: page.form ?? DEFAULT_FORM, columns: root.columns, grid: root.kind,
    variants, chips, sections };
}

function textOf(census) {
  const lines = [`${census.deck} — palette ${census.palette}, widths at ${census.viewport}px`];
  const node = (n, depth) => {
    const cols = n.columns ? `  columns ${n.columns.effective} (authored ${n.columns.authored ?? 'default'}) ${n.grid}` : '';
    const chips = n.chips && n.chips.length ? `  chips ${n.chips.join(',')}` : '';
    const colour = n.variant ? `  ${n.variant}${n.variant_source === 'default' ? ' (default)' : ''}` : '';
    lines.push(`${'  '.repeat(depth)}${n.id}  ${n.kind}  r${n.start.row}c${n.start.col}  ${n.width}${colour}${cols}${chips}`);
    for (const c of n.children || []) node(c, depth + 1);
  };
  for (const p of census.pages) {
    lines.push('', `PAGE ${p.id}  "${p.name}"  ${p.form}  columns ${p.columns.effective} ` +
      `(authored ${p.columns.authored ?? 'default'}) ${p.grid}`);
    for (const c of p.chips) lines.push(`  chip ${c.key}${c.core ? ' (core)' : ''} [${c.scope}]: ${c.members.join(', ') || '(no members)'}`);
    for (const [v, ids] of Object.entries(p.variants)) lines.push(`  colour ${v}: ${ids.join(', ')}`);
    for (const s of p.sections) node(s, 1);
  }
  if (census.problems.length) lines.push('', ...census.problems.map(p => `[FAIL] ${p}`));
  return lines.join('\n');
}

function main() {
  const args = process.argv.slice(2);
  const rootArg = args.find(a => !a.startsWith('--'));
  const root = rootArg ? path.resolve(rootArg) : DEFAULT_ROOT;

  const deck = loadAuthoredDeck(root);
  if (!deck.manifest) {
    console.log(deck.problems.map(p => `[FAIL] ${p}`).join('\n'));
    process.exitCode = 1;
    return;
  }
  const gen = loadGenerated(root);
  const tokens = gen.ok && gen.doc.tokens ? gen.doc.tokens : DEFAULT_TOKENS;
  applyTokens(tokens);
  const built = staticCensus(root);

  const census = {
    deck: deck.manifest.title ?? null, palette: (gen.ok && gen.doc.palette) || deck.manifest.palette || 'neutral',
    viewport: tokens.viewport.w, problems: built.problems,
    pages: deck.pages.map(({ entry, page }) =>
      censusOfPage(entry, page, deck.manifest, tokens.viewport.w, tokens.breakpoints.stack)),
  };
  console.log(args.includes('--json') ? JSON.stringify(census, null, 2) : textOf(census));
  if (census.problems.length) process.exitCode = 1;
}

main();
