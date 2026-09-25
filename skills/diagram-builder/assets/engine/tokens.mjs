// The deck's DESIGN TOKENS: every visual number a deck may tune, with its
// default and its schema. document.yaml `tokens:` is merged over DEFAULT_TOKENS
// by build-data.mjs; the resolved set is written into window.__DOC__, which is
// what the engine turns into CSS custom properties and what both gates read.
// Nothing else in the deck may hold one of these numbers as a literal: a copy
// is how the engine, the static gate and the render gate drift apart.
//
// Pure data plus pure functions over it — no I/O — so the build, the static
// gate and the tests import the same module. The engine does not import it (it
// is a classic script under file://); it applies the `css_vars` projection the
// build writes, so the name mapping below has exactly one owner.
//
// What is NOT here, and why: box chrome (border 1.5, radius, inner gap), the
// half-title clamp of 1, the rail indent depth, the monospace advance and the
// measurement tolerances are fixed. They are craft values, structural
// definitions or facts about the instrument, not choices a deck makes.

const freeze = o => {
  for (const v of Object.values(o)) if (v && typeof v === 'object') freeze(v);
  return Object.freeze(o);
};

export const DEFAULT_TOKENS = freeze({
  row: { cell_h: 130, sep_h: 40, zone_min_h: 180, compact_h: 74 },
  space: { base: 8, scale: [0.5, 1, 2, 3, 4, 6, 8] },
  frame: { v: 28, h: 40, top: 35, narrow: 8 },
  plane_max: 1280,
  cell_min_w: 120,
  type: {
    title: { min_px: 15, vw: 1, max_px: 17, lines: 2 },
    desc: { px: 12, lh: 1.4, lines: 3 },
    kicker: { px: 10.5, track_em: 0.09 },
    section_title: { min_px: 13, vw: 0.85, max_px: 14.5, track_em: 0.1, lines: 2 },
    section_sub: { px: 12, lines: 3 },
    rail: { px: 13, track_em: 0.09 },
    rail_hue: { px: 10.5, track_em: 0, pad_y: 10 },
    panel: { title_px: 19, summary_px: 15, kicker_px: 13, kicker_track_em: 0.08 },
  },
  indent_step: 32,
  dim: { box: 0.18, label: 0.34 },
  panel: { dock: 'bottom-left', inset: 24, aspect: 1.25, width_cols: 2 },
  breakpoints: { stack: 1440, two: 1000, one: 640 },
  viewport: { w: 1920, h: 1080 },
  default_columns: 2,
});

// One entry per leaf path. `min` is a HARD bound, never a suggestion: the
// floors on type sizes, `cell_min_w` and `row.cell_h` are the legibility floor
// (principle 8), so a deck cannot tune its way below readable text.
const px = (min, max, int = false) => ({ type: int ? 'int' : 'number', unit: 'px', min, max });
const em = (min, max) => ({ type: 'number', unit: 'em', min, max });
const num = (min, max, unit = '') => ({ type: 'number', unit, min, max });
const int = (min, max, unit = '') => ({ type: 'int', unit, min, max });
export const TOKEN_SCHEMA = freeze({
  'row.cell_h': px(60, 400, true),
  'row.sep_h': px(16, 120, true),
  'row.zone_min_h': px(0, 600, true),
  'row.compact_h': px(40, 400, true),
  'space.base': px(2, 16),
  'space.scale': { type: 'scale', unit: '× base', length: 7, min: 0.25, max: 16 },
  'frame.v': px(0, 200), 'frame.h': px(0, 200), 'frame.top': px(0, 200), 'frame.narrow': px(0, 64),
  'plane_max': px(640, 7680, true),
  'cell_min_w': px(100, 400, true),
  'type.title.min_px': px(11, 32), 'type.title.vw': num(0, 5, 'vw'), 'type.title.max_px': px(11, 40),
  'type.title.lines': int(1, 4, 'lines'),
  'type.desc.px': px(10, 24), 'type.desc.lh': num(1, 2.4, '× px'), 'type.desc.lines': int(1, 8, 'lines'),
  'type.kicker.px': px(9, 20), 'type.kicker.track_em': em(0, 0.3),
  'type.section_title.min_px': px(10, 32), 'type.section_title.vw': num(0, 5, 'vw'),
  'type.section_title.max_px': px(10, 40), 'type.section_title.track_em': em(0, 0.3),
  'type.section_title.lines': int(1, 4, 'lines'),
  'type.section_sub.px': px(10, 24), 'type.section_sub.lines': int(1, 6, 'lines'),
  'type.rail.px': px(10, 24), 'type.rail.track_em': em(0, 0.3),
  'type.rail_hue.px': px(9, 24), 'type.rail_hue.track_em': em(0, 0.3), 'type.rail_hue.pad_y': px(0, 24),
  'type.panel.title_px': px(12, 48), 'type.panel.summary_px': px(11, 32),
  'type.panel.kicker_px': px(9, 24), 'type.panel.kicker_track_em': em(0, 0.3),
  'indent_step': px(8, 96),
  'dim.box': num(0.05, 0.9, 'opacity'), 'dim.label': num(0.05, 0.9, 'opacity'),
  'panel.dock': { type: 'enum', values: ['bottom-left', 'bottom-right', 'top-left', 'top-right'] },
  'panel.inset': px(0, 96), 'panel.aspect': num(0.5, 3, '× width'), 'panel.width_cols': num(1, 4, 'root columns'),
  'breakpoints.stack': px(320, 7680, true), 'breakpoints.two': px(320, 7680, true), 'breakpoints.one': px(320, 7680, true),
  'viewport.w': px(320, 7680, true), 'viewport.h': px(240, 4320, true),
  'default_columns': int(1, 12, 'columns'),
});

// The only keys a section or a component may override on itself. Row height
// belongs to a grid, so only a section sets it; the two clamps are per box.
// Everything else is deck-wide on purpose: a per-node font size or spacing
// would let one cell quietly stop matching its neighbours.
export const NODE_TOKEN_KEYS = freeze({
  section: ['row.cell_h', 'type.title.lines', 'type.desc.lines'],
  component: ['type.title.lines', 'type.desc.lines'],
});

// A treatment that is sugar for a node override: `compact` IS
// `tokens: { row: { cell_h: <row.compact_h> } }` plus its structural rules
// (tighter row gap and box padding, released description clamp) in index.html.
// An explicit node override wins over the preset.
export const PRESETS = freeze({ compact: { 'row.cell_h': 'row.compact_h' } });

const isMap = v => v !== null && typeof v === 'object' && !Array.isArray(v);

export function getPath(obj, path) {
  return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
}
function setPath(obj, path, value) {
  const keys = path.split('.');
  let o = obj;
  for (const k of keys.slice(0, -1)) o = (o[k] ??= {});
  o[keys[keys.length - 1]] = value;
}
function clone(v) { return JSON.parse(JSON.stringify(v)); }

// Every leaf path of an authored `tokens:` mapping. A mapping where the schema
// expects a leaf (or the reverse) is reported with its path.
function leaves(raw, prefix = '') {
  const out = [];
  for (const [k, v] of Object.entries(raw)) {
    const p = prefix ? `${prefix}.${k}` : k;
    if (isMap(v) && !TOKEN_SCHEMA[p]) out.push(...leaves(v, p)); else out.push([p, v]);
  }
  return out;
}

function checkValue(p, v, where) {
  const s = TOKEN_SCHEMA[p];
  const bad = why => new Error(`[strict-schema] ${where}: tokens.${p} ${why}, got ${JSON.stringify(v)}`);
  if (s.type === 'enum') {
    if (!s.values.includes(v)) throw bad(`must be one of ${s.values.join(', ')}`);
    return;
  }
  if (s.type === 'scale') {
    const ok = Array.isArray(v) && v.length === s.length
      && v.every((x, i) => typeof x === 'number' && x >= s.min && x <= s.max && (i === 0 || x > v[i - 1]));
    if (!ok) throw bad(`must be ${s.length} increasing multipliers of space.base in ${s.min}..${s.max}`);
    return;
  }
  const typed = s.type === 'int' ? Number.isInteger(v) : (typeof v === 'number' && Number.isFinite(v));
  if (!typed || v < s.min || v > s.max)
    throw bad(`must be ${s.type === 'int' ? 'an integer' : 'a number'} ${s.min}..${s.max}${s.unit ? ` ${s.unit}` : ''}`);
}

// The value relations a single bound cannot express.
function checkRelations(t, where) {
  const rel = (ok, msg) => { if (!ok) throw new Error(`[strict-schema] ${where}: ${msg}`); };
  const b = t.breakpoints;
  rel(b.one < b.two && b.two < b.stack,
    `tokens.breakpoints must satisfy one < two < stack, got one ${b.one}, two ${b.two}, stack ${b.stack}`);
  rel(t.type.title.min_px <= t.type.title.max_px, 'tokens.type.title.min_px must not exceed max_px');
  rel(t.type.section_title.min_px <= t.type.section_title.max_px,
    'tokens.type.section_title.min_px must not exceed max_px');
}

// Validate one authored `tokens:` mapping against the keys `allowed` accepts.
// `suggest(key, allowedSet)` is the caller's near-miss hint (build-data.mjs
// passes its field-name suggester, so a typo reads like every other schema error).
function validateAuthored(raw, allowed, where, suggest) {
  if (raw === undefined || raw === null) return [];
  if (!isMap(raw)) throw new Error(`[strict-schema] ${where}: \`tokens\` must be a mapping, got ${JSON.stringify(raw)}`);
  const pairs = leaves(raw);
  for (const [p, v] of pairs) {
    if (!allowed.includes(p)) {
      const known = TOKEN_SCHEMA[p];
      const hint = !known && suggest ? suggest(p, new Set(allowed)) : null;
      throw new Error(`[strict-schema] ${where}: ${known ? `tokens.${p} is deck-wide and cannot be overridden here`
        : `unknown token "${p}"`}${hint ? ` — did you mean "${hint}"?` : ''}` +
        `\n  valid tokens here: ${allowed.join(', ')}`);
    }
    checkValue(p, v, where);
  }
  return pairs;
}

// document.yaml `tokens:` merged over DEFAULT_TOKENS. Throws on an unknown key,
// a value out of its schema range, or a broken relation.
export function resolveDocTokens(raw, suggest) {
  const where = 'document.yaml';
  const out = clone(DEFAULT_TOKENS);
  for (const [p, v] of validateAuthored(raw, Object.keys(TOKEN_SCHEMA), where, suggest)) setPath(out, p, clone(v));
  checkRelations(out, where);
  return out;
}

// A node's resolved override DELTA (preset first, authored wins), or null when
// the node overrides nothing. `kind` is 'section' | 'component'.
export function resolveNodeTokens(node, kind, docTokens, where, suggest) {
  const allowed = NODE_TOKEN_KEYS[kind];
  const out = {};
  const treatments = Array.isArray(node && node.treatment) ? node.treatment : [];
  for (const t of treatments) for (const [p, from] of Object.entries(PRESETS[t] || {}))
    if (allowed.includes(p)) setPath(out, p, getPath(docTokens, from));
  for (const [p, v] of validateAuthored(node && node.tokens, allowed, where, suggest)) setPath(out, p, v);
  return Object.keys(out).length ? out : null;
}

// base ⊕ delta, for the gates: an override inherits down the tree exactly as
// the CSS custom property it becomes does.
export function mergeTokens(base, delta) {
  if (!delta) return base;
  const out = clone(base);
  for (const [p, v] of leaves(delta)) setPath(out, p, clone(v));
  return out;
}

// Token path -> [css custom property, unit]. The engine applies the projection;
// index.html reads each property through var() with the default as fallback.
const CSS_VAR = [
  ['row.cell_h', '--cell-h', 'px'], ['row.sep_h', '--sep-row-h', 'px'], ['row.zone_min_h', '--zone-min-h', 'px'],
  ['frame.v', '--frame-v', 'px'], ['frame.h', '--frame-h', 'px'], ['frame.top', '--frame-top', 'px'],
  ['frame.narrow', '--frame-narrow', 'px'],
  ['plane_max', '--plane-max', 'px'], ['cell_min_w', '--cell-min-w', 'px'],
  ['type.title.min_px', '--title-min', 'px'], ['type.title.vw', '--title-vw', 'vw'],
  ['type.title.max_px', '--title-max', 'px'], ['type.title.lines', '--title-lines', ''],
  ['type.desc.px', '--desc-px', 'px'], ['type.desc.lh', '--desc-lh', ''], ['type.desc.lines', '--desc-lines', ''],
  ['type.kicker.px', '--kicker-px', 'px'], ['type.kicker.track_em', '--kicker-track', 'em'],
  ['type.section_title.min_px', '--ztitle-min', 'px'], ['type.section_title.vw', '--ztitle-vw', 'vw'],
  ['type.section_title.max_px', '--ztitle-max', 'px'], ['type.section_title.track_em', '--ztitle-track', 'em'],
  ['type.section_title.lines', '--ztitle-lines', ''],
  ['type.section_sub.px', '--zsub-px', 'px'], ['type.section_sub.lines', '--zsub-lines', ''],
  ['type.rail.px', '--rail-px', 'px'], ['type.rail.track_em', '--rail-track', 'em'],
  ['type.rail_hue.px', '--rail-hue-px', 'px'], ['type.rail_hue.track_em', '--rail-hue-track', 'em'],
  ['type.rail_hue.pad_y', '--rail-hue-pad-y', 'px'],
  ['type.panel.title_px', '--panel-title-px', 'px'], ['type.panel.summary_px', '--panel-summary-px', 'px'],
  ['type.panel.kicker_px', '--panel-kicker-px', 'px'], ['type.panel.kicker_track_em', '--panel-kicker-track', 'em'],
  ['indent_step', '--indent-step', 'px'],
  ['dim.box', '--dim-box', ''], ['dim.label', '--dim-label', ''],
  ['panel.inset', '--panel-inset', 'px'],
];
const PANEL_DOCK = {
  'bottom-left': { left: 1, bottom: 1 }, 'bottom-right': { right: 1, bottom: 1 },
  'top-left': { left: 1, top: 1 }, 'top-right': { right: 1, top: 1 },
};

// The CSS custom properties for a full token set (`delta` null) or for a node
// override delta (only the properties the delta sets).
export function cssVars(tokens, { delta = false } = {}) {
  const out = {};
  for (const [p, name, unit] of CSS_VAR) {
    const v = getPath(tokens, p);
    if (v !== undefined) out[name] = `${v}${unit}`;
  }
  if (delta) return out;
  tokens.space.scale.forEach((m, i) => { out[`--s-${i + 1}`] = `${+(tokens.space.base * m).toFixed(3)}px`; });
  const dock = PANEL_DOCK[tokens.panel.dock];
  for (const side of ['left', 'right', 'top', 'bottom'])
    out[`--panel-${side}`] = dock[side] ? `${tokens.panel.inset}px` : 'auto';
  return out;
}

// The spacing step `n` (1-based, `--s-n`) in px.
export const space = (tokens, n) => tokens.space.base * tokens.space.scale[n - 1];
