// ─────────────────────────────────────────────────────────────────────────
// static-census.cjs — the browser-free FOUNDATION both guardrails read: the
// authored-data reader, the form taxonomy they share, and the collapse
// breakpoints as the CSS declares them.
// @version 1.1.0  (part of the diagram-builder skill; keep in sync with
//                  engine/build-data.mjs — this module MIRRORS its
//                  visible/order resolution, it does not re-implement it)
//
// WHY THIS IS ITS OWN MODULE
// Two tools need to read the deck's AUTHORED data with no browser involved:
//   • tools/check-layout.mjs — the STATIC gate: parses data/*.yaml and proves the
//     layout closes arithmetically (no render, no Playwright, no dependency
//     beyond the js-yaml the build already uses).
//   • tools/validate-layout.cjs — the RENDER gate: runs this census as a
//     pre-flight before launching Chromium, so a run against stale generated
//     data fails fast.
// It used to live inside validate-layout.cjs, which means anything wanting the
// census had to `require()` a file whose first line is `require('playwright')`.
// That is exactly backwards: the census is the part that must work where NO
// browser exists. Extracting it here is what makes the static gate independent
// of the optional one — the two tools now share ONE parse path (so a divergence
// between them is impossible) and neither pulls the other's dependencies in.
//
// The same reasoning governs everything else here: this file holds what BOTH
// gates need and NEITHER owns. A constant one gate alone consumes belongs in
// that gate, not here — only a value the two would otherwise keep two copies of
// is promoted, because two copies is the drift this module exists to prevent.
// It is deliberately CommonJS and dependency-free (js-yaml is lazy) so the ESM
// static gate and the CJS render gate can both consume it.
//
// EVERY ENTRY POINT TAKES A ROOT. The deck root defaults to the parent of this
// tools/ directory (the normal case), but both functions accept an explicit one
// so a NEGATIVE TEST can point them at a broken fixture in a temp directory
// outside the repo. A guardrail that can only be run against the one deck that
// is supposed to pass has never been shown to fail.
// ─────────────────────────────────────────────────────────────────────────
const path = require('path');
const fs = require('fs');

// The deck root: tools/ lives directly under it.
const DEFAULT_ROOT = path.join(__dirname, '..');

// ── THE SHARED FORM TAXONOMY ───────────────────────────────────────────────
// A page declares its FORM (page YAML `form:`). Both gates scope checks by it,
// so the default and the shared subsets are defined once here rather than once
// per gate. GRIDDED is still consumed by the render gate alone and stays there.
const DEFAULT_FORM = 'dashboard';
// The forms that should EARN a wide canvas by composing sections side by side
// and grouping cells, so a lone cell stranded on its own row is worth failing
// (render invariants P/V, static ROW). A timeline/flow/mindmap may legitimately
// be sparse or linear, so those checks do not judge them.
const GRID_DENSE = new Set(['dashboard', 'comparison', 'planner']);
// WORDFIT — the narrative forms whose cells carry real SYMBOL text: a
// human-language title, and the machine name above it. Those are the forms
// where a token too long for its cell fractures mid-word and reads as a defect;
// a planner's `TODO`/`DONE` code or a timeline's phase label is short by
// construction and is not judged. Consumed by BOTH gates — the render gate's N
// (title token) and the static gate's TEXT budget (title AND kicker token) — so
// it lives here rather than in either one of them.
const WORDFIT = new Set(['dashboard', 'flow']);

// ── THE TOKENS BOTH GATES COMPUTE WITH ─────────────────────────────────────
// Every size, clamp, breakpoint and the presentation viewport come from the
// resolved `window.__DOC__.tokens` the build writes (engine/tokens.mjs is their
// schema and defaults). No gate keeps a mirror of them; staticCensus proves the
// bundle carries every value the YAML authored, so a stale bundle cannot move a
// verdict silently.

// A page opts out of text-fit FAILURES with `text_fit: advisory`: every
// finding is still reported, none fails. Absent means strict.
const isTextFitStrict = page => (page && page.text_fit) !== 'advisory';

// data/data.generated.js is a JS file whose payload is a JSON literal
// (`window.__DOC__ = { ... };`). Sliced out and JSON.parsed — no eval, no module
// load. Returns { ok, doc, problem }.
function loadGenerated(root = DEFAULT_ROOT) {
  const genPath = path.join(root, 'data', 'data.generated.js');
  if (!fs.existsSync(genPath))
    return { ok: false, problem: 'data/data.generated.js does not exist — run `npm run build` first (validate never generates it).' };
  const src = fs.readFileSync(genPath, 'utf8');
  const MARK = 'window.__DOC__ = ';
  const at = src.indexOf(MARK);
  if (at < 0) return { ok: false, problem: `data/data.generated.js has no \`${MARK}\` assignment — it is not a generated deck file. Run \`npm run build\`.` };
  const body = src.slice(at + MARK.length);
  const end = body.indexOf('\n};');
  try { return { ok: true, doc: JSON.parse(end >= 0 ? body.slice(0, end + 2) : body.replace(/;\s*$/, '')) }; }
  catch (e) { return { ok: false, problem: `data/data.generated.js payload is not parseable JSON (${e.message}). Run \`npm run build\`.` }; }
}

// The `max-width` of every `@container stage (…)` block the build generated
// (data/breakpoints.generated.css), descending. Returns { ok, noFile, widths,
// problem }: an absent file is reported, never guessed at.
function cssBreakpoints(root = DEFAULT_ROOT) {
  const file = path.join(root, 'data', 'breakpoints.generated.css');
  if (!fs.existsSync(file))
    return { ok: false, noFile: true, widths: [], problem: `data/breakpoints.generated.css does not exist under "${root}" — run \`npm run build\`` };
  const src = fs.readFileSync(file, 'utf8');
  const widths = [...src.matchAll(/@container\s+stage\s*\(\s*max-width:\s*(\d+)px\s*\)/g)]
    .map(m => Number(m[1]));
  if (!widths.length)
    return { ok: false, widths: [], problem: 'data/breakpoints.generated.css declares no `@container stage (max-width: …)` query' };
  return { ok: true, widths: [...new Set(widths)].sort((a, b) => b - a) };
}

// Every leaf value an authored `tokens:` mapping sets, as [path, value].
function authoredTokenLeaves(raw, prefix = '') {
  const out = [];
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return out;
  for (const [k, v] of Object.entries(raw)) {
    const p = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === 'object' && !Array.isArray(v)) out.push(...authoredTokenLeaves(v, p));
    else out.push([p, v]);
  }
  return out;
}
const tokenAt = (obj, p) => p.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
const sameValue = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// Every token the YAML authors — on the manifest and on each node — must be the
// value the bundle carries, or the gates would judge a deck nobody built.
function tokenProblems(manifest, deckPages, genPages, gen) {
  const out = [];
  if (!gen.tokens || !gen.css_vars) return ['data/data.generated.js carries no `tokens` — run `npm run build`'];
  for (const [p, v] of authoredTokenLeaves(manifest.tokens))
    if (!sameValue(tokenAt(gen.tokens, p), v))
      out.push(`tokens.${p}: document.yaml ${JSON.stringify(v)} != generated ${JSON.stringify(tokenAt(gen.tokens, p))}`);
  const byId = list => { const m = new Map(); (function walk(ns) { for (const n of ns || []) {
    if (n && n.id != null) m.set(String(n.id), n); if (n && Array.isArray(n.children)) walk(n.children); } })(list); return m; };
  for (const { entry, page } of deckPages) {
    const got = genPages.find(p => p && String(p.id) === String(entry.id));
    if (!got) continue;
    const genNodes = byId(got.sections);
    for (const [id, n] of byId(page.sections)) for (const [p, v] of authoredTokenLeaves(n.tokens)) {
      const g = genNodes.get(id);
      if (!g || !sameValue(tokenAt(g.tokens, p), v))
        out.push(`page "${entry.id}" node "${id}" tokens.${p}: yaml ${JSON.stringify(v)} != generated ${JSON.stringify(g && tokenAt(g.tokens, p))}`);
    }
  }
  return out;
}

// js-yaml is resolved LAZILY and its absence is reported as a PROBLEM, never
// thrown and never silently skipped. A census that cannot run is a hole, not a
// pass: skipping it would restore the exact stale-data false green it exists to
// close.
function loadYamlLib() {
  try { return { yaml: require('js-yaml') }; }
  catch (e) {
    return { yaml: null, problem:
      'js-yaml is not resolvable, so the authored YAML could not be read. ' +
      'A guardrail cannot certify data it did not read: install the deck dependencies (`npm install`).' };
  }
}

// ── THE AUTHORED DECK ──────────────────────────────────────────────────────
// Read data/document.yaml + every visible page file it names, resolved in the
// SAME order the build resolves them (build-data.mjs: filter visible !== false,
// then sort by `order`). Tolerant by design: a missing or unparseable page is
// collected as a problem and the walk continues, so one bad file cannot hide
// what is wrong with the rest.
// Returns { ok, problems, manifest, entries, pages: [{entry, page}], dataDir }.
function loadAuthoredDeck(root = DEFAULT_ROOT) {
  const dataDir = path.join(root, 'data');
  const problems = [];
  const { yaml, problem } = loadYamlLib();
  if (!yaml) return { ok: false, problems: [problem], pages: [], dataDir };

  const manifestPath = path.join(dataDir, 'document.yaml');
  if (!fs.existsSync(manifestPath))
    return { ok: false, problems: [`data/document.yaml does not exist under "${root}".`], pages: [], dataDir };

  let manifest;
  try { manifest = yaml.load(fs.readFileSync(manifestPath, 'utf8')); }
  catch (e) { return { ok: false, problems: [`data/document.yaml is not parseable YAML (${e.message}).`], pages: [], dataDir }; }
  if (!manifest || !Array.isArray(manifest.pages))
    return { ok: false, problems: ['data/document.yaml has no top-level `pages` list.'], pages: [], dataDir };

  const entries = manifest.pages.filter(p => p && p.visible !== false)
    .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));

  const pages = [];
  for (const entry of entries) {
    const file = path.join(dataDir, entry.file || '');
    if (!entry.file || !fs.existsSync(file)) {
      problems.push(`page "${entry.id}": file "${entry.file}" is missing on disk`); continue;
    }
    let page;
    try { page = yaml.load(fs.readFileSync(file, 'utf8')); }
    catch (e) { problems.push(`page "${entry.id}": ${entry.file} is not parseable YAML (${e.message})`); continue; }
    if (!page || typeof page !== 'object') {
      problems.push(`page "${entry.id}": ${entry.file} did not parse to a mapping`); continue;
    }
    pages.push({ entry, page });
  }
  return { ok: problems.length === 0, problems, manifest, entries, pages, dataDir };
}

// ─────────────────────────────────────────────────────────────────────────
// STATIC CENSUS (pre-flight): data/*.yaml  vs  data/data.generated.js
//
// `validate` is DECOUPLED from `build` ON PURPOSE (it is pure-read), which has one
// sharp edge: it asserts the LAST BUILT data, and nothing ever told you the build
// was stale. Edit a YAML, forget `npm run build`, run `npm run render` — it goes
// green on the OLD deck and you read that as a verdict on the change you just
// made. That is a false green with no defect anywhere in the geometry.
//
// So before the browser is even launched, re-parse the YAML with the same js-yaml
// the build uses and compare a CENSUS of it against the generated file (which is
// JSON literal behind `window.__DOC__ = `). A mismatch does not try to guess which
// side is right — it says RUN BUILD, and exits non-zero.
//
// This stays a CENSUS, not a re-implementation of the build: page identity/order,
// palette, per-page form/layout/columns, filter keys, and the node counts. It is an
// INDEPENDENT recount, which is exactly why it catches drift.
// Runs BEFORE Chromium, so a stale-data run fails fast and needs no browser at all.
// ─────────────────────────────────────────────────────────────────────────
function nodeCensus(sections) {
  const out = { sections: 0, boxes: 0, seps: 0, rails: 0, spacers: 0, halves: 0, ids: [] };
  const isSectionNode = n => !!(n && Array.isArray(n.children));
  (function walk(list) {
    for (const n of list || []) {
      if (n && n.id != null) out.ids.push(String(n.id));
      if (isSectionNode(n)) { out.sections++; walk(n.children); continue; }
      if (n && Array.isArray(n.treatment) && n.treatment.includes('half')) out.halves++;
      // The chain ends in `else out.boxes++`, so every type NOT named here is
      // counted as a box. A `spacer` therefore needs its own arm or the census
      // recounts it as content and the drift it exists to catch goes unseen.
      if (n && n.type === 'separator') out.seps++;
      else if (n && n.type === 'rail') out.rails++;
      else if (n && n.type === 'spacer') out.spacers++;
      else out.boxes++;
    }
  })(sections);
  out.ids.sort();
  return out;
}

function pageCensus(p) {
  const n = nodeCensus(p.sections);
  return { id: String(p.id), form: p.form ?? null, layout: p.layout ?? null,
    columns: p.columns ?? null,
    filters: (p.filters || []).map(f => f && f.key).filter(Boolean).sort(),
    ...n };
}

function staticCensus(root = DEFAULT_ROOT) {
  const dataDir = path.join(root, 'data');
  const genPath = path.join(dataDir, 'data.generated.js');
  const problems = [];

  const deck = loadAuthoredDeck(root);
  // A deck that could not be READ is reported as-is: there is nothing to compare
  // the generated file against, and guessing would be worse than saying so.
  if (!deck.manifest) return { ok: false, problems: deck.problems };

  const loaded = loadGenerated(root);
  if (!loaded.ok) return { ok: false, problems: [loaded.problem] };
  const gen = loaded.doc;

  const manifest = deck.manifest;
  // Any page that could not be read at all is a census problem in its own right.
  problems.push(...deck.problems);

  if ((manifest.palette ?? 'neutral') !== (gen.palette ?? 'neutral'))
    problems.push(`palette: document.yaml "${manifest.palette ?? 'neutral'}" != generated "${gen.palette ?? 'neutral'}"`);
  problems.push(...tokenProblems(manifest, deck.pages, Array.isArray(gen.pages) ? gen.pages : [], gen));
  if ((manifest.title ?? null) !== (gen.title ?? null))
    problems.push(`title: document.yaml "${manifest.title}" != generated "${gen.title}"`);

  const genPages = Array.isArray(gen.pages) ? gen.pages : [];
  const wantIds = deck.entries.map(e => String(e.id));
  const gotIds = genPages.map(p => String(p && p.id));
  if (wantIds.join('|') !== gotIds.join('|'))
    problems.push(`page list: document.yaml [${wantIds.join(', ')}] != generated [${gotIds.join(', ')}]`);

  for (const { entry, page } of deck.pages) {
    const want = pageCensus(page);
    const got = genPages.find(p => p && String(p.id) === String(entry.id));
    if (!got) { problems.push(`page "${entry.id}" is authored but absent from data.generated.js`); continue; }
    const gotC = pageCensus(got);
    for (const k of ['form', 'layout', 'columns', 'sections', 'boxes', 'seps', 'rails', 'spacers', 'halves'])
      if (String(want[k]) !== String(gotC[k]))
        problems.push(`page "${entry.id}" ${k}: yaml ${JSON.stringify(want[k])} != generated ${JSON.stringify(gotC[k])}`);
    if (want.filters.join('|') !== gotC.filters.join('|'))
      problems.push(`page "${entry.id}" filter keys: yaml [${want.filters.join(', ')}] != generated [${gotC.filters.join(', ')}]`);
    if (want.ids.join('|') !== gotC.ids.join('|')) {
      const onlyYaml = want.ids.filter(i => !gotC.ids.includes(i));
      const onlyGen = gotC.ids.filter(i => !want.ids.includes(i));
      problems.push(`page "${entry.id}" node ids differ` +
        (onlyYaml.length ? ` — only in yaml: [${onlyYaml.join(', ')}]` : '') +
        (onlyGen.length ? ` — only in generated: [${onlyGen.join(', ')}]` : ''));
    }
  }
  return { ok: problems.length === 0, problems,
    summary: `${wantIds.length} page(s), palette "${gen.palette ?? 'neutral'}"` };
}

module.exports = { DEFAULT_ROOT, DEFAULT_FORM, GRID_DENSE, WORDFIT, cssBreakpoints,
  isTextFitStrict, loadGenerated, loadAuthoredDeck, nodeCensus, pageCensus, staticCensus };
