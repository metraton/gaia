// Build step: reads data/document.yaml (manifest) + data/pages/*.yaml,
// resolves visible/order, and emits data/data.generated.js — a plain
// `window.__DOC__ = {...}` assignment, so index.html can load it via a
// normal <script src> with zero runtime fetch/CORS concerns under file://.
//
// @version 2.0.0  (part of the diagram-builder skill; keep the engine generation
//                  in sync with engine/engine.js + tools/validate-layout.cjs)
//
// Run: npm run build  (or: node engine/build-data.mjs)
// Re-run whenever a YAML file under data/ changes.
import yaml from 'js-yaml';
import { readFileSync, writeFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

// This script lives in engine/; the data lives in ../data.
const ROOT = dirname(fileURLToPath(import.meta.url));
const DATA_DIR = join(ROOT, '..', 'data');

function readYaml(path) {
  return yaml.load(readFileSync(path, 'utf8'));
}

// ── STRICT SCHEMA ──────────────────────────────────────────────────────────
// The build is the single gate every YAML edit passes through, so it is where a
// TYPO or an INVENTED field must be caught — loudly — instead of being silently
// dropped by the engine at render time (a misspelled `colummns` or a made-up
// `higlight` used to just do nothing, with no signal). Each node kind has a
// WHITELIST of the fields the engine actually reads; any key outside it is a
// hard error that names the page, the node, the offending key, and (when close)
// the field it was probably meant to be.
//
// The whitelists mirror EXACTLY what engine/engine.js consumes:
//   • page (root section): id/layout/columns/filters/sections + `form`
//     (the FORM the layout guardrail scopes its invariants by) and the
//     manifest-owned identity keys (name/order/visible) in case a page file
//     carries them.
//   • section (a node WITH `children`): buildSection + sectionHeader + the
//     per-child grid props (order/span/rowspan).
//   • component (a leaf, no `children`): buildBox / buildSeparator / buildRail
//     + the per-child grid props.
const PAGE_FIELDS = new Set([
  'id', 'layout', 'columns', 'filters', 'sections', 'form',
  'name', 'order', 'visible']);
const SECTION_FIELDS = new Set([
  'id', 'title', 'subtitle', 'variant',
  'order', 'span', 'rowspan', 'columns', 'children']);
const COMPONENT_FIELDS = new Set([
  'id', 'type', 'variant', 'variant_extra', 'status', 'title', 'description',
  'detail', 'note', 'order', 'span', 'rowspan', 'filters',
  'orientation', 'style', 'text']);

// Cheap Levenshtein — only used to suggest the intended field on a rejection,
// so a typo ("colummns") points straight at the real key ("columns").
function editDistance(a, b) {
  const m = a.length, n = b.length;
  const d = Array.from({ length: m + 1 }, (_, i) => [i, ...Array(n).fill(0)]);
  for (let j = 0; j <= n; j++) d[0][j] = j;
  for (let i = 1; i <= m; i++)
    for (let j = 1; j <= n; j++)
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1,
        d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
  return d[m][n];
}
function suggest(field, allowed) {
  let best = null, bestD = Infinity;
  for (const cand of allowed) {
    const dist = editDistance(field.toLowerCase(), cand.toLowerCase());
    if (dist < bestD) { bestD = dist; best = cand; }
  }
  // only suggest when it is plausibly the same word (edit distance ≤ ~1/3 len)
  return bestD <= Math.max(2, Math.ceil(field.length / 3)) ? best : null;
}
function checkFields(obj, allowed, kind, pageId, where) {
  if (!obj || typeof obj !== 'object') return;
  for (const key of Object.keys(obj)) {
    if (allowed.has(key)) continue;
    const hint = suggest(key, allowed);
    throw new Error(
      `[strict-schema] page "${pageId}" ${where}: unknown ${kind} field "${key}"` +
      (hint ? ` — did you mean "${hint}"?` : '') +
      `\n  valid ${kind} fields: ${[...allowed].join(', ')}`);
  }
}
// Recursively validate every node under a page's `sections`. A node WITH a
// `children` array is a section (recurse into it); otherwise it is a leaf
// component. Runs at build time, before the engine ever sees the data.
function validateNode(node, pageId, where) {
  const isSection = Array.isArray(node && node.children);
  const kind = isSection ? 'section' : 'component';
  const id = (node && node.id) || '(no id)';
  const label = `${where} ${kind} "${id}"`;
  checkFields(node, isSection ? SECTION_FIELDS : COMPONENT_FIELDS, kind, pageId, label);
  if (isSection)
    node.children.forEach((c, i) => validateNode(c, pageId, `${label} >`));
}
function validatePageSchema(page) {
  checkFields(page, PAGE_FIELDS, 'page', page.id, 'root');
  for (const sec of page.sections || []) validateNode(sec, page.id, 'root >');
}

const manifest = readYaml(join(DATA_DIR, 'document.yaml'));
if (!manifest || !Array.isArray(manifest.pages)) {
  throw new Error('document.yaml must have a top-level `pages` list');
}

const pages = manifest.pages
  .filter(p => p.visible !== false)
  .sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
  .map(entry => {
    if (!entry.file) throw new Error(`page "${entry.id}" is missing "file"`);
    const page = readYaml(join(DATA_DIR, entry.file));
    if (!page || page.id !== entry.id) {
      throw new Error(`manifest id "${entry.id}" does not match page.id "${page && page.id}" in ${entry.file}`);
    }
    // STRICT SCHEMA: reject any unknown field in the page, its sections, or its
    // components BEFORE the engine silently drops it. Runs on the raw page file.
    validatePageSchema(page);
    // manifest owns name/order/visible; page file owns everything else.
    return { ...page, name: entry.name, order: entry.order };
  });

const doc = {
  title: manifest.title,
  subtitle: manifest.subtitle,
  // optional — passthrough only, no default here; the seed document.yaml
  // pre-populates it. Absent from the manifest -> absent on window.__DOC__ ->
  // engine.js's `if (barVer && doc.version)` guard skips rendering cleanly.
  version: manifest.version,
  pages
};

const out = `// GENERATED FILE — do not edit by hand.
// Produced by build-data.mjs from data/document.yaml + data/pages/*.yaml.
window.__DOC__ = ${JSON.stringify(doc, null, 2)};
`;

writeFileSync(join(DATA_DIR, 'data.generated.js'), out, 'utf8');
console.log(`Wrote data/data.generated.js — ${pages.length} visible page(s): ${pages.map(p => p.id).join(', ')}`);
