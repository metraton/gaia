// Build step: reads data/document.yaml (manifest) + data/pages/*.yaml,
// resolves visible/order, and emits data/data.generated.js — a plain
// `window.__DOC__ = {...}` assignment, so index.html can load it via a
// normal <script src> with zero runtime fetch/CORS concerns under file://.
//
// @version 2.1.0  (part of the diagram-builder skill; keep the engine generation
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
//   • manifest (document.yaml): the deck identity + `palette` + the page list.
//   • page (root section): id/layout/columns/filters/sections + `form`
//     (the FORM the layout guardrail scopes its invariants by) and the
//     manifest-owned identity keys (name/order/visible) in case a page file
//     carries them.
//   • section (a node WITH `children`): buildSection + sectionHeader + the
//     per-child grid props (order/span/rowspan) + both vocabulary axes
//     (variant/treatment).
//   • component (a leaf, no `children`): buildBox / buildSeparator / buildRail /
//     buildSpacer + the per-child grid props + both vocabulary axes. A `spacer`
//     is narrowed further by SPACER_FIELDS below — it is the one leaf that reads
//     NO payload at all.
//   • filter (an entry of `filters[]`): the chip's key/label/steps. Filters used
//     to bypass this gate entirely — a typo in a `key` produced no error, just a
//     chip that silently dimmed the whole canvas because nothing matched it.
const MANIFEST_FIELDS = new Set(['title', 'subtitle', 'version', 'palette', 'pages']);
const MANIFEST_PAGE_FIELDS = new Set(['id', 'name', 'order', 'visible', 'file']);
const PAGE_FIELDS = new Set([
  'id', 'layout', 'columns', 'filters', 'sections', 'form',
  'name', 'order', 'visible']);
const SECTION_FIELDS = new Set([
  'id', 'title', 'subtitle', 'variant', 'treatment',
  'order', 'span', 'rowspan', 'columns', 'children']);
const COMPONENT_FIELDS = new Set([
  'id', 'type', 'variant', 'variant_extra', 'treatment', 'kicker', 'title',
  'description', 'detail', 'note', 'order', 'span', 'rowspan', 'filters',
  'style', 'text']);
const FILTER_FIELDS = new Set(['key', 'label', 'steps']);

// ── THE TWO ORTHOGONAL AXES ────────────────────────────────────────────────
// `variant` and `treatment` answer two DIFFERENT questions, and conflating them
// is what forced `centered` to be smuggled in through `variant_extra`:
//
//   variant   — WHAT DOES THIS MEAN? The semantic COLOUR role, in the idea's own
//               language of risk / state / kind. Exactly ONE value: a thing is
//               not simultaneously "at risk" and "hardened".
//   treatment — HOW IS THIS DRAWN? Structural / presentational modifiers that are
//               orthogonal to meaning: whether a frame is drawn at all, how the
//               content is aligned, how much of its slot the component occupies,
//               which way it runs. A LIST: they compose freely, and composing two
//               of them is normal, not exceptional.
//
// With one closed single-valued field it was impossible to say "this is at risk"
// AND "this goes without a frame" at once. Two axes make that the default case.
//
// Both enums are CLOSED and validated here. A structural value written into
// `variant` is a HARD ERROR that names the axis it belongs to — a clean break, not
// a silent translation, so a deck is either on the new vocabulary or it fails
// loudly at the gate. (The legacy→new mapping is tabled in the skill's
// reference.md, "Migrating a pre-2.1 deck".)
const COMPONENT_VARIANTS = new Set(['neutral', 'good', 'warn', 'bad', 'accent', 'muted']);
const SECTION_VARIANTS = new Set(['neutral', 'good', 'bad']);
const COMPONENT_TREATMENTS = new Set(['centered', 'half', 'vertical', 'outside']);
const SECTION_TREATMENTS = new Set(['plain', 'envelope']);
// Which axis a value belongs to, for the error message. A value that MOVED axes
// gets a targeted "that is a treatment, not a variant" error instead of a bare
// "unknown value", because the author's intent is unambiguous and the fix is one
// mechanical edit.
const TREATMENT_OWNER = {
  plain: 'section', envelope: 'section',
  centered: 'component', half: 'component', vertical: 'component', outside: 'component',
};

// Document palettes. A palette is a SKIN — the semantic roles are identical in
// all of them (see the palette token blocks in index.html), so switching one can
// never change what a deck means. Validated here so a typo (`pallete:`) fails
// instead of silently falling back to neutral.
const PALETTES = new Set(['neutral', 'rose-pine', 'rose-pine-moon', 'contrast']);

// ── CLOSED VALUE ENUMS: form / layout / type / style ───────────────────────
// The whitelists above close the KEY space; these close the VALUE space of the
// four fields the engine or the guardrail DISPATCHES on. Until now only the keys
// were checked, so a typo in one of these VALUES passed the gate and then failed
// SILENTLY downstream — and each of the three below is a real observed false
// green, not a hypothetical:
//   • `form: dashboards` — the guardrail scopes its invariant table by form
//     MEMBERSHIP, so an undeclared form matched no row at all and the page was
//     reported with ZERO checks ("ALL PASS — 0 checks", exit 0). Closed here at
//     the door AND fail-closed at the guardrail (invariant A + the `total === 0`
//     gate in tools/validate-layout.cjs).
//   • `layout: gird` — engine.js's `renderable` filter DROPS the page with a
//     console.warn nobody reads, so the page silently vanishes from the deck.
//     Closing it is also what makes the guardrail's page CENSUS sound: rendered
//     `.act` count can only equal `__DOC__.pages.length` if no page can be
//     dropped at render time.
//   • `type: seperator` — the leaf dispatch in buildGrid falls through to
//     buildBox, so the typo renders an EMPTY card that every invariant happily
//     counts as a filled cell.
//   • `style: dotetd` — buildSeparator's `sep.style === 'dotted'` ternary
//     silently yields 'solid'. The mildest of the four; closed for symmetry, so
//     no dispatched value is left unchecked.
// Same shape as PALETTES above: a closed Set, a `.has()` gate, and a `suggest()`
// near-miss hint naming the valid values.
//
// FORMS is the SAME six names as `FORMS` in tools/validate-layout.cjs and must be
// kept in sync with it — that table is the consumer of this field.
const FORMS = new Set(['dashboard', 'timeline', 'flow', 'comparison', 'mindmap', 'planner']);
// The engine renders exactly one page layout (engine.js: `(p.layout || 'grid') === 'grid'`).
const LAYOUTS = new Set(['grid']);
// The leaf `type` dispatch in engine.js buildGrid: separator | rail | spacer |
// anything else => box. Closed to the four the engine actually builds.
const COMPONENT_TYPES = new Set(['box', 'separator', 'rail', 'spacer']);
// A `spacer` is the DECLARED HOLE: a leaf that occupies its cell and draws
// nothing, so a rectangle can be closed without inventing content for it
// (principle 9 — the hole speaks: close it, or declare it). Being an OCCUPANT and
// not a modifier is the whole of it, which is why the two merge dials are the
// only fields it keeps: a hole two tracks wide, or three rows tall, is as
// legitimate a hole as a single cell, and `span`/`rowspan` belong to the CELL
// rather than to its content.
//
// Everything else is rejected BY NAME rather than ignored. Every remaining
// component field presupposes ink: the payload slots (kicker/title/description/
// detail/note) are what buildBox renders and buildSpacer never reads, `variant`/
// `variant_extra` colour a frame that is not drawn, every `treatment` aligns or
// divides content that does not exist (and `half` would pair it into a slot it
// cannot share), `style` is the separator's line, and `filters` would make the
// spacer a MEMBER of a relation it can never light — inflating a chip's arity
// with an invisible end. A spacer carrying a title is not a spacer; it is an
// empty card, which is the thing this type exists to stop being authored.
const SPACER_FIELDS = new Set(['id', 'type', 'order', 'span', 'rowspan']);
// buildSeparator's line style. Only a `separator` reads it.
const SEPARATOR_STYLES = new Set(['solid', 'dotted']);

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

// One value of a CLOSED VALUE ENUM (form / layout / type / style). Same shape as
// the PALETTES gate above — a `.has()` test plus a `suggest()` near-miss hint —
// factored into one function only because four fields now need the identical
// message, exactly as checkVariantValue already does for the colour axis. An
// absent value is legal: every one of these four fields has an engine default.
function checkEnumValue(value, allowed, what, pageId, label) {
  if (value === undefined || value === null) return;
  const hint = suggest(String(value), allowed);
  if (allowed.has(value)) return;
  throw new Error(
    `[strict-schema] page "${pageId}" ${label}: unknown ${what} "${value}"` +
    (hint ? ` — did you mean "${hint}"?` : '') +
    `\n  valid ${what} values: ${[...allowed].join(', ')}`);
}

// ── AXIS VALUE VALIDATION ──────────────────────────────────────────────────
// One value of `variant` against the COLOUR enum for this node kind. A value that
// belongs to the OTHER axis produces a targeted error naming `treatment`, since
// that is exactly the confusion the split exists to end.
function checkVariantValue(value, kind, pageId, label) {
  if (value === undefined || value === null) return;
  const allowed = kind === 'section' ? SECTION_VARIANTS : COMPONENT_VARIANTS;
  if (allowed.has(value)) return;
  if (TREATMENT_OWNER[value]) {
    throw new Error(
      `[strict-schema] page "${pageId}" ${label}: "${value}" is a STRUCTURAL TREATMENT, not a colour variant.\n` +
      `  \`variant\` carries ONE semantic COLOUR role; structural modifiers live on \`treatment\` (a list).\n` +
      `  write:  treatment: [${value}]\n` +
      `  valid ${kind} variant values: ${[...allowed].join(', ')}`);
  }
  throw new Error(
    `[strict-schema] page "${pageId}" ${label}: unknown ${kind} variant "${value}"` +
    (suggest(value, allowed) ? ` — did you mean "${suggest(value, allowed)}"?` : '') +
    `\n  valid ${kind} variant values: ${[...allowed].join(', ')}` +
    `\n  (structural modifiers are not variants — see \`treatment\`: ${[...(kind === 'section' ? SECTION_TREATMENTS : COMPONENT_TREATMENTS)].join(', ')})`);
}

// `treatment` must be a LIST of values from this node kind's closed treatment set.
// A colour role written here gets the mirror-image error of checkVariantValue —
// the two axes reject each other's values symmetrically, so neither can drift into
// the other.
function checkTreatment(node, kind, pageId, label) {
  const t = node.treatment;
  if (t === undefined || t === null) return [];
  if (!Array.isArray(t)) {
    throw new Error(
      `[strict-schema] page "${pageId}" ${label}: \`treatment\` must be a LIST (structural modifiers compose), got ${typeof t}.\n` +
      `  write:  treatment: [${JSON.stringify(t).replace(/"/g, '')}]`);
  }
  const allowed = kind === 'section' ? SECTION_TREATMENTS : COMPONENT_TREATMENTS;
  const other = kind === 'section' ? SECTION_VARIANTS : COMPONENT_VARIANTS;
  for (const v of t) {
    if (allowed.has(v)) continue;
    if (other.has(v)) {
      throw new Error(
        `[strict-schema] page "${pageId}" ${label}: "${v}" is a semantic COLOUR role, not a structural treatment.\n` +
        `  write:  variant: ${v}\n` +
        `  valid ${kind} treatment values: ${[...allowed].join(', ')}`);
    }
    // A treatment that exists but on the OTHER node kind — the most likely real
    // mistake (a `plain` on a box, a `half` on a section), so it is named as such.
    if (TREATMENT_OWNER[v]) {
      throw new Error(
        `[strict-schema] page "${pageId}" ${label}: treatment "${v}" applies to a ${TREATMENT_OWNER[v]}, not a ${kind}.\n` +
        `  valid ${kind} treatment values: ${[...allowed].join(', ')}`);
    }
    throw new Error(
      `[strict-schema] page "${pageId}" ${label}: unknown ${kind} treatment "${v}"` +
      (suggest(v, allowed) ? ` — did you mean "${suggest(v, allowed)}"?` : '') +
      `\n  valid ${kind} treatment values: ${[...allowed].join(', ')}`);
  }
  const dupes = t.filter((v, i) => t.indexOf(v) !== i);
  if (dupes.length)
    throw new Error(`[strict-schema] page "${pageId}" ${label}: duplicate treatment "${dupes[0]}"`);
  return t;
}

// ── TREATMENT COMBINATION RULES ────────────────────────────────────────────
// A treatment declares a CONSEQUENCE in the layout, so some combinations are
// contradictions and some payloads no longer fit. Rejecting them here is what
// keeps the guardrail's invariants true BY CONSTRUCTION rather than by luck:
//
//   half + description  → a half-height component has ~63px of box; a title plus
//                         three clamped description lines cannot fit, and the
//                         overflow would trip invariant C (no box clipping). `half`
//                         is the TITLE-ONLY treatment by design — put the prose in
//                         `detail`, which lives in the click panel anyway.
//   vertical + description → same reason: the text block is rotated, so a
//                         description has no horizontal room to wrap into.
//   half + rowspan      → a contradiction of axes: `rowspan` grows a cell by WHOLE
//                         slots, `half` divides ONE slot. Together they have no
//                         meaning.
//   half on a section   → rejected by checkTreatment (section treatments are
//                         plain/envelope); a section is not a slot occupant.
function checkTreatmentCombinations(node, treatments, pageId, label) {
  const has = v => treatments.includes(v);
  const titleOnly = ['half', 'vertical'].filter(has);
  if (titleOnly.length && node.description !== undefined && node.description !== null) {
    throw new Error(
      `[strict-schema] page "${pageId}" ${label}: treatment "${titleOnly[0]}" is TITLE-ONLY — remove \`description\`.\n` +
      `  A ${titleOnly[0]} component has no room to render description lines (it would clip, breaking invariant C).\n` +
      `  Move the text to \`detail\`: it shows in the click-through panel, which is where long copy belongs.`);
  }
  if (has('half') && Math.max(1, Math.floor(Number(node.rowspan) || 1)) > 1) {
    throw new Error(
      `[strict-schema] page "${pageId}" ${label}: treatment "half" and \`rowspan\` are mutually exclusive.\n` +
      `  \`rowspan\` grows a cell by WHOLE slots; "half" divides ONE slot. Pick one.`);
  }
}

// A `spacer` keeps ONLY the geometry fields (see SPACER_FIELDS). The rejection
// names the axis the key belongs to and what it would have drawn, because the
// author's intent is unambiguous in every case: a payload on a spacer means the
// cell was meant to CARRY something, and then it is a box.
function checkSpacer(node, pageId, label) {
  for (const key of Object.keys(node)) {
    if (SPACER_FIELDS.has(key)) continue;
    throw new Error(
      `[strict-schema] page "${pageId}" ${label}: a \`spacer\` carries no "${key}".\n` +
      `  A spacer is the DECLARED HOLE — it occupies its cell and draws NOTHING, so it reads no payload,\n` +
      `  no colour role, no treatment and no filter key. It is not an empty card.\n` +
      `  valid spacer fields: ${[...SPACER_FIELDS].join(', ')}\n` +
      `  If the cell is meant to carry "${key}", it is a box: drop \`type: spacer\`.`);
  }
}

// HALF PAIRING. `half` does not shrink a cell — it DIVIDES a slot: two half
// components stack inside ONE full-height grid slot, so the rectangle stays full
// and no hole appears. That only works in PAIRS, and the pair is formed from
// CONSECUTIVE half leaves in render order (the author controls which two by
// placing them adjacently). Two rules are enforced here rather than left to the
// renderer:
//   • a run of consecutive halves must be EVEN — an odd one out would occupy half
//     a slot and leave the other half empty, which is precisely the "hole" the
//     governing definition forbids. Loud beats a silent gap.
//   • both members of a pair must declare the SAME `span` — they share one slot,
//     so a disagreement has no coherent rendering.
// Runs over a section's children in the SAME order the engine renders them.
function checkHalfPairing(children, pageId, label) {
  const isHalfLeaf = c => c && !Array.isArray(c.children) &&
    Array.isArray(c.treatment) && c.treatment.includes('half');
  const ordered = [...(children || [])]
    .map((c, i) => ({ c, i }))
    .sort((a, b) => { const oa = a.c.order ?? (a.i + 1), ob = b.c.order ?? (b.i + 1);
      return oa === ob ? a.i - b.i : oa - ob; })
    .map(x => x.c);
  let run = [];
  const flush = () => {
    if (!run.length) return;
    if (run.length % 2 !== 0) {
      throw new Error(
        `[strict-schema] page "${pageId}" ${label}: ${run.length} consecutive "half" component(s) — must be an EVEN number.\n` +
        `  "half" DIVIDES a slot: two halves stack inside one full-height cell. An odd half would fill\n` +
        `  half a slot and leave the rest empty — a hole, which the layout model forbids.\n` +
        `  unpaired: "${run[run.length - 1].id || '(no id)'}" — add a partner beside it, or drop its "half" treatment.`);
    }
    for (let i = 0; i < run.length; i += 2) {
      const a = run[i], b = run[i + 1];
      const sa = Math.max(1, Number(a.span) || 1), sb = Math.max(1, Number(b.span) || 1);
      if (sa !== sb) {
        throw new Error(
          `[strict-schema] page "${pageId}" ${label}: half pair "${a.id || '?'}" (span ${sa}) + "${b.id || '?'}" (span ${sb}) disagree on \`span\`.\n` +
          `  Both halves share ONE slot, so they must declare the same span.`);
      }
    }
    run = [];
  };
  for (const c of ordered) { if (isHalfLeaf(c)) run.push(c); else flush(); }
  flush();
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
  // A spacer's narrow whitelist is applied BEFORE the two vocabulary axes, so a
  // `variant` written on one is reported as "a spacer carries no variant" rather
  // than as a near-miss inside a colour enum it has no business reaching.
  if (!isSection && node.type === 'spacer') { checkSpacer(node, pageId, label); return; }
  checkVariantValue(node.variant, kind, pageId, label);
  const treatments = checkTreatment(node, kind, pageId, label);
  if (!isSection) {
    // The leaf `type` is a DISPATCH: buildGrid routes separator | rail | (default)
    // box. An unrecognized value does not error there, it falls through to
    // buildBox — so `type: seperator` renders an empty card that every layout
    // invariant counts as a legitimately filled cell. Closed here at the door.
    checkEnumValue(node.type, COMPONENT_TYPES, 'component type', pageId, label);
    // `style` is the separator's line style; validated whenever it is present so
    // no dispatched value escapes the gate. (Whether `style` BELONGS on a
    // non-separator is a separate, narrower question — the field whitelist still
    // permits it on any component.)
    checkEnumValue(node.style, SEPARATOR_STYLES, 'separator style', pageId, label);
    // `variant_extra` is the narrow escape hatch for a SECOND COLOUR role (e.g. a
    // `bad` box that is also a `muted` secondary): the risk axis and the kind axis are
    // genuinely different dimensions, and a single-valued `variant` cannot carry
    // both. It is validated against the SAME colour enum as `variant`, which is
    // what closes the old hole — a structural value can no longer hide in here,
    // it must go on `treatment`.
    for (const extra of node.variant_extra || [])
      checkVariantValue(extra, kind, pageId, `${label} variant_extra`);
    checkTreatmentCombinations(node, treatments, pageId, label);
  }
  if (isSection) {
    checkHalfPairing(node.children, pageId, label);
    node.children.forEach(c => validateNode(c, pageId, `${label} >`));
  }
}

// Validate the chips of a `filters[]` list. Previously NOT validated at all: a
// typo in a `key` was invisible — the chip rendered, matched nothing, and dimmed
// the entire canvas with no error anywhere. The referential half of this (every
// declared chip has a member, every referenced key is declared) is asserted on the
// real render by invariant K in tools/validate-layout.cjs; here we only guarantee
// the SHAPE.
function validateFilters(filters, pageId, where) {
  if (filters === undefined || filters === null) return;
  if (!Array.isArray(filters))
    throw new Error(`[strict-schema] page "${pageId}" ${where}: \`filters\` must be a LIST of chips, got ${typeof filters}`);
  const seen = new Set();
  filters.forEach((f, i) => {
    const label = `${where} filter #${i + 1}`;
    if (!f || typeof f !== 'object' || Array.isArray(f))
      throw new Error(`[strict-schema] page "${pageId}" ${label}: each filter must be a mapping with \`key\` and \`label\``);
    checkFields(f, FILTER_FIELDS, 'filter', pageId, `${label} "${f.key || '(no key)'}"`);
    if (typeof f.key !== 'string' || !f.key.trim())
      throw new Error(`[strict-schema] page "${pageId}" ${label}: a filter needs a non-empty string \`key\` (the slug components reference)`);
    if (typeof f.label !== 'string' || !f.label.trim())
      throw new Error(`[strict-schema] page "${pageId}" ${label} "${f.key}": a filter needs a non-empty \`label\` (the text on the chip)`);
    if (f.steps !== undefined && !Array.isArray(f.steps))
      throw new Error(`[strict-schema] page "${pageId}" ${label} "${f.key}": \`steps\` must be a LIST of explanation lines`);
    if (seen.has(f.key))
      throw new Error(`[strict-schema] page "${pageId}" ${label}: duplicate filter key "${f.key}"`);
    seen.add(f.key);
  });
}

function validatePageSchema(page) {
  checkFields(page, PAGE_FIELDS, 'page', page.id, 'root');
  // `form` SCOPES the guardrail's invariant table by membership, so an undeclared
  // value silently reduced the page's applicable invariant set to the EMPTY set —
  // a page reported as "ALL PASS — 0 checks" with exit 0. `layout` gates whether
  // the page renders at all (engine.js drops a non-`grid` page with a warn).
  // Neither has any recoverable meaning when misspelled, so both fail at the door.
  checkEnumValue(page.form, FORMS, 'page form', page.id, 'root');
  checkEnumValue(page.layout, LAYOUTS, 'page layout', page.id, 'root');
  validateFilters(page.filters, page.id, 'root');
  checkHalfPairing(page.sections, page.id, 'root');
  for (const sec of page.sections || []) validateNode(sec, page.id, 'root >');
}

const manifest = readYaml(join(DATA_DIR, 'document.yaml'));
if (!manifest || !Array.isArray(manifest.pages)) {
  throw new Error('document.yaml must have a top-level `pages` list');
}
checkFields(manifest, MANIFEST_FIELDS, 'manifest', '(document.yaml)', 'root');
manifest.pages.forEach((p, i) =>
  checkFields(p, MANIFEST_PAGE_FIELDS, 'manifest page', '(document.yaml)', `pages[${i}] "${(p && p.id) || '?'}"`));

// PALETTE — document-level skin selector. Absent means `neutral`, which is the
// palette every pre-2.1 deck renders with, so omitting it is a no-op.
const palette = manifest.palette ?? 'neutral';
if (!PALETTES.has(palette)) {
  throw new Error(
    `[strict-schema] document.yaml: unknown palette "${palette}"` +
    (suggest(String(palette), PALETTES) ? ` — did you mean "${suggest(String(palette), PALETTES)}"?` : '') +
    `\n  valid palettes: ${[...PALETTES].join(', ')}`);
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
    // STRICT SCHEMA: reject any unknown field in the page, its sections, its
    // components, or its filters BEFORE the engine silently drops it. Runs on the
    // raw page file.
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
  palette,
  pages
};

// The generated file APPLIES THE PALETTE ITSELF, before the deck renders. It is
// loaded by a <script src> in <head>-order ahead of engine.js and before any
// content paints, so setting `data-palette` here avoids the flash of neutral that
// waiting for engine.js's mount would cause. Guarded so the file stays harmless if
// it is ever loaded outside a browser.
const out = `// GENERATED FILE — do not edit by hand.
// Produced by build-data.mjs from data/document.yaml + data/pages/*.yaml.
window.__DOC__ = ${JSON.stringify(doc, null, 2)};
if (typeof document !== 'undefined' && document.documentElement)
  document.documentElement.setAttribute('data-palette', window.__DOC__.palette || 'neutral');
`;

writeFileSync(join(DATA_DIR, 'data.generated.js'), out, 'utf8');
console.log(`Wrote data/data.generated.js — palette "${palette}", ${pages.length} visible page(s): ${pages.map(p => p.id).join(', ')}`);
