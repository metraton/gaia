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
import { resolveDocTokens, resolveNodeTokens, cssVars } from './tokens.mjs';
import chips from './chips.cjs';

const { resolvePageFilters } = chips;

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
//     NO payload at all — and a `rail` by RAIL_FIELDS.
//   • filter (an entry of `filters[]`): the chip's key/label/steps. Filters used
//     to bypass this gate entirely — a typo in a `key` produced no error, just a
//     chip that silently dimmed the whole canvas because nothing matched it.
//   • `tokens` (manifest, section, box): the design tokens of engine/tokens.mjs.
//     The manifest may set any of them; a section or a box only the few in
//     NODE_TOKEN_KEYS. The presentation viewport is `tokens.viewport`.
//   • core chips: the manifest's `filters:` are inherited by every page, first
//     and in order (engine/chips.cjs); a page's manifest entry drops one only by
//     naming it in `omit_filters`, so the deck's chip coverage reads in one file.
//     `harmony: true` opts the deck into the static gate's HARMONY check.
const MANIFEST_FIELDS = new Set(['title', 'subtitle', 'version', 'palette', 'palette_overrides', 'tokens', 'filters', 'harmony', 'pages']);
const MANIFEST_PAGE_FIELDS = new Set(['id', 'name', 'order', 'visible', 'file', 'omit_filters']);
const PAGE_FIELDS = new Set([
  'id', 'layout', 'columns', 'filters', 'sections', 'form', 'text_fit',
  'name', 'order', 'visible']);

// `text_fit` decides whether text that overflows its cell at the presentation
// viewport FAILS the static gate (strict, the default) or is only reported
// (advisory). The gates read the value from the bundle, so it is validated here.
const TEXT_FIT = new Set(['strict', 'advisory']);

const SECTION_FIELDS = new Set([
  'id', 'title', 'subtitle', 'variant', 'treatment',
  'order', 'span', 'rowspan', 'columns', 'children', 'tokens']);
const COMPONENT_FIELDS = new Set([
  'id', 'type', 'variant', 'variant_extra', 'treatment', 'kicker', 'title',
  'description', 'detail', 'note', 'order', 'span', 'rowspan', 'filters',
  'style', 'text', 'copy', 'tokens', 'lead']);
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
// blue / violet / gold / clay are CATEGORICAL: they tell peer groups apart and
// carry no risk or state, so the page that uses them must say what each means.
const COMPONENT_VARIANTS = new Set([
  'neutral', 'good', 'warn', 'bad', 'accent', 'muted',
  'blue', 'violet', 'gold', 'clay']);
const SECTION_VARIANTS = new Set(['neutral', 'good', 'bad']);
const COMPONENT_TREATMENTS = new Set(['centered', 'half', 'vertical', 'outside']);
// `middle` centres a section's grid vertically inside the height its compound
// row stretches it to, so a short cell beside taller neighbours leaves no gap.
// `compact` gives one leaf grid a shorter row (index.html `.zone.compact`), for
// a staircase that must end level with a shorter neighbour; the uniform-row
// gates (validate U) exempt only grids that declare it.
const SECTION_TREATMENTS = new Set(['plain', 'envelope', 'middle', 'compact']);
// Which axis a value belongs to, for the error message. A value that MOVED axes
// gets a targeted "that is a treatment, not a variant" error instead of a bare
// "unknown value", because the author's intent is unambiguous and the fix is one
// mechanical edit.
const TREATMENT_OWNER = {
  plain: 'section', envelope: 'section', middle: 'section', compact: 'section',
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
// A `rail` is a title-only label, so it keeps the geometry, its title and its
// treatment. `filters` is its one membership field: buildRail stamps it as
// `data-filters`, so a chip lights the rail exactly as it lights a box. Its
// colour is limited to the four categorical hues, the only variants
// `.rail.<hue>` draws in index.html. `indent` (0..RAIL_MAX_INDENT) insets the
// drawn frame inside its cell, one step per tree level, so a tree reads by
// indentation while the cell itself still fills its track.
const RAIL_FIELDS = new Set(['id', 'type', 'order', 'span', 'rowspan', 'title', 'treatment', 'variant', 'filters', 'indent']);
const RAIL_VARIANTS = new Set(['blue', 'violet', 'gold', 'clay']);
const RAIL_MAX_INDENT = 3;
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
//                         plain/envelope/middle/compact); a section is not a slot
//                         occupant.
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
  const isRail = !isSection && node.type === 'rail';
  checkFields(node, isSection ? SECTION_FIELDS : isRail ? RAIL_FIELDS : COMPONENT_FIELDS,
    isRail ? 'rail' : kind, pageId, label);
  // A spacer's narrow whitelist is applied BEFORE the two vocabulary axes, so a
  // `variant` written on one is reported as "a spacer carries no variant" rather
  // than as a near-miss inside a colour enum it has no business reaching.
  if (!isSection && node.type === 'spacer') { checkSpacer(node, pageId, label); return; }
  if (isRail) {
    checkEnumValue(node.variant, RAIL_VARIANTS, 'rail variant', pageId, label);
    if (node.indent !== undefined && !(Number.isInteger(node.indent) && node.indent >= 0 && node.indent <= RAIL_MAX_INDENT))
      throw new Error(`[strict-schema] page "${pageId}" ${label}: rail \`indent\` must be an integer 0..${RAIL_MAX_INDENT}, got ${JSON.stringify(node.indent)}`);
  }
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
    if (node.variant_extra !== undefined) deprecated.variant_extra.push(`${pageId} > ${id}`);
    checkTreatmentCombinations(node, treatments, pageId, label);
    // `copy` opts a box into a copy-to-clipboard button: `true` copies its title
    // verbatim, a string copies that string. Only buildBox draws the button.
    if (node.copy !== undefined) {
      const isBox = node.type === undefined || node.type === 'box';
      const valid = node.copy === true || (typeof node.copy === 'string' && node.copy.length > 0);
      if (!isBox || !valid)
        throw new Error(`[strict-schema] page "${pageId}" ${label}: \`copy\` must be \`true\` or a non-empty string, and only on a box; got ${JSON.stringify(node.copy)} on type "${node.type ?? 'box'}"`);
      if (node.copy === true && !node.title)
        throw new Error(`[strict-schema] page "${pageId}" ${label}: \`copy: true\` copies the title, and this box has none`);
    }
  }
  if (isSection) {
    if (treatments.includes('compact') && node.children.some(c => Array.isArray(c && c.children)))
      throw new Error(`[strict-schema] page "${pageId}" ${label}: treatment "compact" shortens the rows of ONE leaf grid, so its children must all be components, not sections.`);
    checkHalfPairing(node.children, pageId, label);
    node.children.forEach(c => validateNode(c, pageId, `${label} >`));
  }
  resolveNodeOverride(node, kind, pageId, label);
}

// A node's `tokens:` is replaced IN THE BUNDLE by its resolved override delta
// (a `compact` preset included) and the CSS properties the engine sets inline.
// Only a box reads the two clamps, so a separator or a rail carrying `tokens`
// is refused rather than ignored.
function resolveNodeOverride(node, kind, pageId, label) {
  const isBox = kind === 'component' && (node.type === undefined || node.type === 'box');
  if (node.tokens !== undefined && kind === 'component' && !isBox)
    throw new Error(`[strict-schema] page "${pageId}" ${label}: \`tokens\` applies to a box or a section, not a ${node.type}`);
  const delta = resolveNodeTokens(node, kind, tokens, `page "${pageId}" ${label}`, suggest);
  if (delta) { node.tokens = delta; node.css_vars = cssVars(delta, { delta: true }); }
  else delete node.tokens;
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

// THE LEAD BAND. `lead: true` marks the box that states the page's claim: a
// plain box, so every gate already measures it, placed as the page's first band
// (a direct root child, first in `order`, spanning every root column). The
// marker is what makes it the one box the HARMONY check exempts.
function checkLead(page) {
  const leads = [];
  (function walk(list, atRoot) {
    for (const n of list || []) {
      if (n && n.lead !== undefined) leads.push({ n, atRoot });
      if (Array.isArray(n && n.children)) walk(n.children, false);
    }
  })(page.sections, true);
  const where = id => `[strict-schema] page "${page.id}" lead "${id || '(no id)'}"`;
  const first = [...(page.sections || [])].map((c, i) => ({ c, eff: c.order ?? (i + 1), i }))
    .sort((a, b) => a.eff - b.eff || a.i - b.i)[0];
  const cols = page.columns ?? tokens.default_columns;
  for (const { n, atRoot } of leads) {
    if (n.lead !== true) throw new Error(`${where(n.id)}: \`lead\` is \`true\` or absent, got ${JSON.stringify(n.lead)}`);
    if (Array.isArray(n.children) || (n.type ?? 'box') !== 'box')
      throw new Error(`${where(n.id)}: only a box can be the lead band`);
    if (!atRoot || first.c !== n)
      throw new Error(`${where(n.id)}: the lead band is the page's FIRST band — a direct child of the page root, first in \`order\``);
    if ((n.span ?? 1) !== cols)
      throw new Error(`${where(n.id)}: the lead band spans the whole page — write \`span: ${cols}\` (the root's columns)`);
    const t = n.treatment || [];
    if (t.includes('half') || t.includes('vertical'))
      throw new Error(`${where(n.id)}: a lead band is a horizontal band; "half" and "vertical" do not apply`);
  }
}

// Fields kept only for old decks: collected while validating and reported once
// per build, so an author sees every use without the build failing.
const deprecated = { layout: [], variant_extra: [] };

function validatePageSchema(page) {
  checkFields(page, PAGE_FIELDS, 'page', page.id, 'root');
  if (page.layout !== undefined) deprecated.layout.push(page.id);
  // `form` SCOPES the guardrail's invariant table by membership, so an undeclared
  // value silently reduced the page's applicable invariant set to the EMPTY set —
  // a page reported as "ALL PASS — 0 checks" with exit 0. `layout` gates whether
  // the page renders at all (engine.js drops a non-`grid` page with a warn).
  // Neither has any recoverable meaning when misspelled, so both fail at the door.
  checkEnumValue(page.form, FORMS, 'page form', page.id, 'root');
  checkEnumValue(page.layout, LAYOUTS, 'page layout', page.id, 'root');
  checkEnumValue(page.text_fit, TEXT_FIT, 'page text_fit', page.id, 'root');
  validateFilters(page.filters, page.id, 'root');
  checkHalfPairing(page.sections, page.id, 'root');
  for (const sec of page.sections || []) validateNode(sec, page.id, 'root >');
  checkLead(page);
}

const manifest = readYaml(join(DATA_DIR, 'document.yaml'));
if (!manifest || !Array.isArray(manifest.pages)) {
  throw new Error('document.yaml must have a top-level `pages` list');
}
checkFields(manifest, MANIFEST_FIELDS, 'manifest', '(document.yaml)', 'root');
manifest.pages.forEach((p, i) =>
  checkFields(p, MANIFEST_PAGE_FIELDS, 'manifest page', '(document.yaml)', `pages[${i}] "${(p && p.id) || '?'}"`));

// TOKENS — document.yaml `tokens:` over DEFAULT_TOKENS, validated. Resolved
// before any page, because a node override is validated against it.
const tokens = resolveDocTokens(manifest.tokens, suggest);

// CORE CHIPS — validated like a page's chips, then inherited by every page.
validateFilters(manifest.filters, '(document.yaml)', 'root');
if (manifest.harmony !== undefined && typeof manifest.harmony !== 'boolean')
  throw new Error(`[strict-schema] document.yaml: \`harmony\` is true or false, got ${JSON.stringify(manifest.harmony)}`);

// PALETTE — document-level skin selector. Absent means `neutral`, which is the
// palette every pre-2.1 deck renders with, so omitting it is a no-op.
const palette = manifest.palette ?? 'neutral';
if (!PALETTES.has(palette)) {
  throw new Error(
    `[strict-schema] document.yaml: unknown palette "${palette}"` +
    (suggest(String(palette), PALETTES) ? ` — did you mean "${suggest(String(palette), PALETTES)}"?` : '') +
    `\n  valid palettes: ${[...PALETTES].join(', ')}`);
}

// PALETTE OVERRIDES — a deck's own colour for a palette token, per theme. The key
// set is exactly the tokens tools/contrast-audit.cjs pairs read (the audit refuses
// an override it has no pair for), so every override a deck ships is measured
// against WCAG; the value syntax is the one that audit parses.
const PALETTE_OVERRIDE_KEYS = new Set([
  'bg', 'surface', 'surface2', 'zone', 'ink', 'body', 'muted', 'line', 'zone-line',
  'crit', 'crit-soft', 'warn', 'warn-soft', 'olive', 'olive-soft', 'strong', 'strong-soft',
  'clay', 'clay-soft',
  ...['blue', 'violet', 'gold', 'clay'].flatMap(h => [`hue-${h}`, `hue-${h}-soft`]),
]);
const COLOUR_VALUE = /^(#[0-9a-f]{3}|#[0-9a-f]{6}|rgba?\(\s*[\d.]+\s*,\s*[\d.]+\s*,\s*[\d.]+\s*(,\s*[\d.]+\s*)?\))$/i;
function readPaletteOverrides(raw) {
  if (raw === undefined) return undefined;
  const where = '[strict-schema] document.yaml: `palette_overrides`';
  if (!raw || typeof raw !== 'object' || Array.isArray(raw))
    throw new Error(`${where} is a map of \`light\` and/or \`dark\`, got ${JSON.stringify(raw)}`);
  const out = {};
  for (const [theme, tokensOf] of Object.entries(raw)) {
    if (theme !== 'light' && theme !== 'dark')
      throw new Error(`${where}: unknown theme "${theme}" — write \`light\` or \`dark\``);
    if (!tokensOf || typeof tokensOf !== 'object' || Array.isArray(tokensOf))
      throw new Error(`${where}.${theme} is a map of token: colour, got ${JSON.stringify(tokensOf)}`);
    out[theme] = {};
    for (const [key, value] of Object.entries(tokensOf)) {
      if (!PALETTE_OVERRIDE_KEYS.has(key)) {
        const hint = suggest(String(key), PALETTE_OVERRIDE_KEYS);
        throw new Error(`${where}.${theme}: "${key}" is not an overridable colour token` +
          (hint ? ` — did you mean "${hint}"?` : '') + `\n  overridable: ${[...PALETTE_OVERRIDE_KEYS].join(', ')}`);
      }
      if (typeof value !== 'string' || !COLOUR_VALUE.test(value.trim()))
        throw new Error(`${where}.${theme}.${key}: "${value}" is not a colour — write #rgb, #rrggbb, rgb() or rgba()`);
      out[theme][`--${key}`] = value.trim();
    }
  }
  return out;
}
const paletteOverrides = readPaletteOverrides(manifest.palette_overrides);

// The override rules outrank the palette blocks in index.html by specificity
// (0,3,1 against 0,2,1), not by source order: the generated script inserts them at
// load, and where it lands in <head> relative to the inline <style> is not fixed.
function paletteOverrideCss(overrides) {
  const rule = (sel, vars) => `${sel} { ${Object.entries(vars).map(([k, v]) => `${k}:${v};`).join(' ')} }`;
  const rules = [];
  if (overrides?.light && Object.keys(overrides.light).length)
    rules.push(rule('html:not(.dark)[data-palette]:root', overrides.light));
  if (overrides?.dark && Object.keys(overrides.dark).length)
    rules.push(rule('html.dark[data-palette]:root', overrides.dark));
  return rules.join('\n');
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
    const filters = resolvePageFilters(manifest.filters, entry.omit_filters, page.filters, page.id);
    if (filters.length || page.filters !== undefined) page.filters = filters;
    // manifest owns name/order/visible; page file owns everything else.
    return { ...page, name: entry.name, order: entry.order };
  });

// `layout` has one value the engine renders, so it selects nothing; and a second
// colour role puts two claims on one frame's fill and border, where principle 5
// wants one claim per channel. Both still build, so old decks keep rendering.
if (deprecated.layout.length)
  console.warn(`[deprecated] \`layout\` on ${deprecated.layout.length} page(s) (${deprecated.layout.join(', ')}): ` +
    'the engine renders one page layout, so the field selects nothing. Delete it; a later version will refuse it.');
if (deprecated.variant_extra.length)
  console.warn(`[deprecated] \`variant_extra\` on ${deprecated.variant_extra.length} component(s) ` +
    `(${deprecated.variant_extra.join(', ')}): a second colour role puts two claims on one frame. Keep one ` +
    '`variant` and say the other in the kicker, a treatment or a legend band; a later version will refuse it.');

const doc = {
  title: manifest.title,
  subtitle: manifest.subtitle,
  // optional — passthrough only, no default here; the seed document.yaml
  // pre-populates it. Absent from the manifest -> absent on window.__DOC__ ->
  // engine.js's `if (barVer && doc.version)` guard skips rendering cleanly.
  version: manifest.version,
  palette,
  palette_overrides: paletteOverrides,
  // The resolved tokens are what both gates read; `css_vars` is the projection
  // engine.js applies to :root. Per-node overrides ride on the node itself.
  tokens,
  css_vars: cssVars(tokens),
  pages
};

// ── THE BREAKPOINTS, GENERATED ─────────────────────────────────────────────
// A container query cannot read var(), so the three collapse tiers are the one
// part of the stylesheet the build writes (data/breakpoints.generated.css, linked
// by index.html). Every rule inside still spends tokens through var().
//   stack — compound grids stop laying sections side by side: they fold into a
//           column and every child keeps its content height (a flex-basis would
//           size the HEIGHT in column direction), stretched to the full width.
//           align-content:stretch is needed beside align-items: in a
//           column-direction wrap flex the single line otherwise shrink-wraps.
//   two   — every multi-column leaf grid steps to the 2-track intermediate; a
//           partial span keeps its proportion (--span2) and the separator rows
//           are re-derived for that track count (--row-tracks-2).
//   one   — the endpoint: every leaf grid is one track, a partial span becomes a
//           full band, every separator row is thin (--row-tracks-1), and the
//           canvas chrome shrinks to the narrow frame. The :not(.sec-c1) twins
//           match the 1000px tier's specificity so they win by source order.
function breakpointsCss(bp) {
  return `/* GENERATED FILE — do not edit by hand.
   Produced by engine/build-data.mjs from tokens.breakpoints (${bp.stack} / ${bp.two} / ${bp.one}px). */
@container stage (max-width: ${bp.stack}px) {
  .sec-grid.sec-compound { flex-direction:column; align-items:stretch; align-content:stretch; }
  .sec-plane > .sec-grid.sec-compound { align-items:stretch; align-content:stretch; }
  .sec-grid.sec-compound > * { flex:0 0 auto; }
  .sec-grid.sec-compound > .msp { flex:0 0 auto; }
  .sec-grid.sec-compound > .zone { flex:0 0 auto; }
  .sec-grid.sec-compound > .box { align-self:stretch; }
  .sec-plane > .sec-grid.sec-compound:has(> .msp) {
    display:flex; flex-direction:column; align-items:stretch; align-content:stretch;
    grid-template-columns:none; }
  .sec-plane > .sec-grid.sec-compound:has(> .msp) > .msp {
    align-self:stretch; }
}
@container stage (max-width: ${bp.two}px) {
  .sec-grid:not(.sec-compound):not(.sec-c1) { grid-template-columns:repeat(2, minmax(0,1fr)); }
  .sec-grid:not(.sec-compound):not(.sec-c1) > .mspan { grid-column:span var(--span2, 1); }
  .sec-grid:not(.sec-compound):not(.sec-c1) { grid-auto-rows:var(--row-tracks-2, var(--cell-h, 130px)); }
}
@container stage (max-width: ${bp.one}px) {
  .canvas { left:var(--frame-narrow, 8px); right:var(--frame-narrow, 8px); padding:var(--frame-narrow, 8px); }
  .sec-grid:not(.sec-compound) { grid-template-columns:minmax(0,1fr); }
  .sec-grid:not(.sec-compound):not(.sec-c1) { grid-template-columns:minmax(0,1fr); }
  .sec-grid:not(.sec-compound) > .mspan { grid-column:1 / -1; }
  .sec-grid:not(.sec-compound):not(.sec-c1) > .mspan { grid-column:1 / -1; }
  .sec-grid:not(.sec-compound):not(.sec-c1) { grid-auto-rows:var(--row-tracks-1, var(--cell-h, 130px)); }
}
`;
}
writeFileSync(join(DATA_DIR, 'breakpoints.generated.css'), breakpointsCss(tokens.breakpoints), 'utf8');

// The generated file APPLIES THE PALETTE ITSELF, before the deck renders. It is
// loaded by a <script src> in <head>-order ahead of engine.js and before any
// content paints, so setting `data-palette` here avoids the flash of neutral that
// waiting for engine.js's mount would cause. Guarded so the file stays harmless if
// it is ever loaded outside a browser. The palette overrides ride the same early
// path, as one inserted <style>, for the same reason.
const overrideCss = paletteOverrideCss(paletteOverrides);
const out = `// GENERATED FILE — do not edit by hand.
// Produced by build-data.mjs from data/document.yaml + data/pages/*.yaml.
window.__DOC__ = ${JSON.stringify(doc, null, 2)};
if (typeof document !== 'undefined' && document.documentElement)
  document.documentElement.setAttribute('data-palette', window.__DOC__.palette || 'neutral');
${overrideCss ? `if (typeof document !== 'undefined' && document.head) {
  const s = document.createElement('style');
  s.setAttribute('data-palette-overrides', '');
  s.textContent = ${JSON.stringify(overrideCss)};
  document.head.appendChild(s);
}
` : ''}`;

writeFileSync(join(DATA_DIR, 'data.generated.js'), out, 'utf8');
console.log(`Wrote data/data.generated.js + data/breakpoints.generated.css — palette "${palette}", ${pages.length} visible page(s): ${pages.map(p => p.id).join(', ')}`);
