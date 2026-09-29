// census.mjs — what each page IS, per page and in reading order, for a reader
// who holds the deck against the sketch agreed with the user: its sections and
// how they nest, the width the grid gives every node, the colours and the chips.
//
// Run: npm run census [-- --json] [-- <deckRoot>]
//
// Every id, variant and chip key is the one the YAML authors, so a difference
// from the sketch can be named. Widths are RESOLVED, not authored: `columns` and
// `span` go through the model's own grow-with-content clamp (check-layout.mjs),
// and `width` is what the grid gives the node at the presentation viewport
// (`tokens.viewport.w`):
//   "w/n"          w of the n tracks of a real grid
//   "flex s/t"     a nested section sharing a flex row by span (t = the row's total)
//   "content"      a component sitting directly in a flex row, sized by its content
//   "full"         the whole row
// A section's `grid` says which of those its children get: "tracks" (a leaf grid,
// always a CSS grid), "root" (the page's section grid) or "row" (a nested flex
// row); "root" and "row" stack into one column at or below the stack breakpoint.
//
// The census reads the authored YAML and computes with the tokens of the last
// build; it exits non-zero when that build no longer matches the YAML, because a
// census of a deck nobody built would validate the wrong thing.
import path from 'node:path';
import censusLib from './static-census.cjs';
import { applyTokens, tracksFor, widthAtTier, slotsOf, effectiveCols, orderedChildren } from './check-layout.mjs';
import { DEFAULT_TOKENS } from '../engine/tokens.mjs';

const { DEFAULT_ROOT, DEFAULT_FORM, loadAuthoredDeck, loadGenerated, staticCensus } = censusLib;

const isSection = n => !!(n && Array.isArray(n.children));
const kindOf = n => (isSection(n) ? 'section' : (n && n.type) || 'box');

// The grid a page root or a section lays its children out in.
function gridOf(node, children, isRoot) {
  const compound = children.some(isSection);
  const cols = effectiveCols(node.columns, slotsOf(children), compound);
  const spanOf = n => Math.max(1, Math.min(Number(n && n.span) || 1, cols));
  // Mirrors check-layout.mjs sectionOuterWidth: a nested row divides its width
  // among the sections that are not bands, by span.
  const flexTotal = children.filter(n => isSection(n) && spanOf(n) < cols)
    .reduce((t, n) => t + spanOf(n), 0) || 1;
  return { kind: !compound ? 'tracks' : isRoot ? 'root' : 'row', cols, spanOf, flexTotal,
    columns: { authored: node.columns ?? null, effective: cols } };
}

function widthIn(grid, child, span, viewportW, stackW) {
  if (grid.kind === 'tracks') {
    const tracks = tracksFor(grid.cols, viewportW);
    return `${widthAtTier(span, grid.cols, tracks)}/${tracks}`;
  }
  if (viewportW <= stackW || span >= grid.cols) return 'full';
  if (grid.kind === 'root') return `${span}/${grid.cols}`;
  return isSection(child) ? `flex ${span}/${grid.flexTotal}` : 'content';
}

function censusOfPage(entry, page, manifest, viewportW, stackW) {
  const variants = {};
  const chips = (page.filters || []).map(f => ({ key: f.key, label: f.label ?? null, core: false, members: [] }));
  const coreKeys = new Set((manifest.filters || []).map(f => f.key));
  for (const chip of chips) chip.core = coreKeys.has(chip.key);

  const describe = (node, grid) => {
    const span = grid.spanOf(node);
    const out = {
      id: node.id ?? null, kind: kindOf(node), title: node.title ?? null,
      variant: node.variant ?? null,
      span: { authored: node.span ?? null, resolved: span, rows: node.rowspan ?? 1 },
      width: widthIn(grid, node, span, viewportW, stackW),
    };
    if (Array.isArray(node.treatment) && node.treatment.length) out.treatment = node.treatment;
    if (node.variant != null) (variants[node.variant] ||= []).push(out.id);
    const keys = Array.isArray(node.filters) ? node.filters : [];
    if (!isSection(node)) out.chips = keys;
    for (const key of keys) chips.find(c => c.key === key)?.members.push(out.id);
    if (isSection(node)) {
      const inner = gridOf(node, node.children, false);
      Object.assign(out, { columns: inner.columns, grid: inner.kind });
      out.children = orderedChildren(node.children).map(({ c }) => describe(c, inner));
    }
    return out;
  };

  const root = gridOf(page, page.sections || [], true);
  const sections = orderedChildren(page.sections).map(({ c }) => describe(c, root));
  return { id: String(entry.id), name: entry.name ?? null, order: entry.order ?? null,
    form: page.form ?? DEFAULT_FORM, columns: root.columns, grid: root.kind,
    variants, chips, sections };
}

function textOf(census) {
  const lines = [`${census.deck} — palette ${census.palette}, widths at ${census.viewport}px`];
  const node = (n, depth) => {
    const cols = n.columns ? `  columns ${n.columns.effective} (authored ${n.columns.authored ?? 'default'}) ${n.grid}` : '';
    const chips = n.chips && n.chips.length ? `  chips ${n.chips.join(',')}` : '';
    lines.push(`${'  '.repeat(depth)}${n.id}  ${n.kind}  ${n.width}${n.variant ? `  ${n.variant}` : ''}${cols}${chips}`);
    for (const c of n.children || []) node(c, depth + 1);
  };
  for (const p of census.pages) {
    lines.push('', `PAGE ${p.id}  "${p.name}"  ${p.form}  columns ${p.columns.effective} ` +
      `(authored ${p.columns.authored ?? 'default'}) ${p.grid}`);
    for (const c of p.chips) lines.push(`  chip ${c.key}${c.core ? ' (core)' : ''}: ${c.members.join(', ') || '(no members)'}`);
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
    deck: deck.manifest.title ?? null, palette: deck.manifest.palette ?? 'neutral',
    viewport: tokens.viewport.w, problems: built.problems,
    pages: deck.pages.map(({ entry, page }) =>
      censusOfPage(entry, page, deck.manifest, tokens.viewport.w, tokens.breakpoints.stack)),
  };
  console.log(args.includes('--json') ? JSON.stringify(census, null, 2) : textOf(census));
  if (census.problems.length) process.exitCode = 1;
}

main();
